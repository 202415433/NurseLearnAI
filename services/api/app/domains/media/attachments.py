"""Rattachement des médias aux versions de leçons (2.2.3-e, correction D3-3).

L'API n'a AUCUN droit d'écriture sur `lesson_version_media` : elle appelle les fonctions contrôlées
`nl_media_attach` / `nl_media_detach` (migration 0010, SECURITY DEFINER), qui passent par le
déclencheur de 0007. Contrôles appliqués, dans l'ordre :

1. session valide (session sensible pour les écritures) ;
2. version de leçon visible dans la portée de l'utilisateur (sinon 404, existence non révélée) ;
3. `content:create` dans la portée de la leçon (403) ;
4. version en BROUILLON (409 `not_draft`) : une version soumise, approuvée ou publiée — donc toute
   édition figée — n'est jamais modifiée, ni directement ni par rattachement ;
5. média visible dans la portée de l'utilisateur (404 sinon : aucun média d'un autre établissement
   n'est révélé) ;
6. média public, ou du MÊME établissement que la leçon (422 `media_scope`) ;
7. média reçu, en cours d'analyse ou sain ; jamais en attente, infecté, en échec d'analyse, révoqué,
   supprimé ou purgé (422 `media_not_clean`) — la soumission exige ensuite `clean` (0007) et la
   diffusion exige `clean` + un verdict valide (2.2.3-d).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.core.errors import ProblemError
from app.domains.editorial.errors import BASE, problem
from app.domains.editorial.scope import can_read, ensure_can, load, not_found
from app.domains.editorial.tx import editorial_tx, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, parse_uuid
from app.domains.identity.sessions import AuthContext

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : médias des leçons"])

ATTACHABLE = ("quarantined", "scanning", "clean")

LIST_SQL = """
SELECT m.id, m.kind, m.mime_type, m.state, m.display_name, m.byte_size, m.organization_id, m.alt_text_i18n,
       m.captions_for, m.legal_hold, x.attached_at, x.attached_by,
       (SELECT c.id FROM media_assets c WHERE c.captions_for = m.id AND c.state = 'clean'
           AND c.deleted_at IS NULL ORDER BY c.created_at DESC LIMIT 1) AS captions_id
  FROM lesson_version_media x JOIN media_assets m ON m.id = x.media_id
 WHERE x.lesson_version_id = :lv
 ORDER BY x.attached_at, m.id
"""


def attachment_out(row: Any) -> dict[str, Any]:
    return {
        "media_id": row["id"],
        "kind": row["kind"],
        "mime_type": row["mime_type"],
        "state": row["state"],
        "display_name": row["display_name"],
        "byte_size": row["byte_size"],
        "organization_id": row["organization_id"],
        "alt_text": row["alt_text_i18n"],
        "captions_for": row["captions_for"],
        "captions_id": row["captions_id"],
        "legal_hold": row["legal_hold"],
        "attached_at": row["attached_at"],
        "attached_by": row["attached_by"],
    }


async def _attachments(db: Any, lv: uuid.UUID) -> list[dict[str, Any]]:
    return [attachment_out(r) for r in (await db.execute(text(LIST_SQL), {"lv": lv})).mappings()]


async def _media_in_scope(db: Any, ctx: AuthContext, media_id: uuid.UUID) -> Any:
    row = (
        (
            await db.execute(
                text("SELECT id, organization_id, state, deleted_at, purged_at FROM media_assets WHERE id = :id"),
                {"id": media_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or not can_read(ctx, row["organization_id"]):
        raise not_found("Média")
    return row


def _media_scope_problem() -> ProblemError:
    return ProblemError(
        422,
        "Un média d'établissement ne peut être rattaché qu'à une leçon du même établissement",
        type_=BASE + "publication-requirements",
        extra={"code": "media_scope"},
    )


@router.get("/lesson-versions/{version_id}/media", summary="Médias rattachés à une version de leçon")
async def list_attachments(version_id: str, ctx: Auth, db: Db) -> dict[str, Any]:
    entity = await load(db, ctx, "lesson-versions", parse_uuid(version_id), what="Version de leçon")
    editable = entity.status == "draft" and ctx.can("content:create", entity.org)
    return {
        "lesson_version_id": entity.id,
        "status": entity.status,
        "editable": editable,
        "items": await _attachments(db, entity.id),
    }


@router.put(
    "/lesson-versions/{version_id}/media/{media_id}",
    summary="Rattacher un média à un brouillon (fonction contrôlée ; idempotent)",
)
async def attach_media(
    version_id: str, media_id: str, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    vid, mid = parse_uuid(version_id), parse_uuid(media_id)
    async with editorial_tx(db, ctx):
        entity = await load(db, ctx, "lesson-versions", vid, lock=True, what="Version de leçon")
        ensure_can(ctx, "content:create", entity.org)
        if entity.status != "draft":
            raise problem("not_draft")
        media = await _media_in_scope(db, ctx, mid)
        if media["organization_id"] is not None and media["organization_id"] != entity.org:
            raise _media_scope_problem()
        if media["deleted_at"] is not None or media["purged_at"] is not None or media["state"] not in ATTACHABLE:
            raise problem("media_not_clean", detail="Média non rattachable dans son état actuel")
        created = (await db.execute(text("SELECT nl_media_attach(:lv, :m)"), {"lv": vid, "m": mid})).scalar()
        if created:
            await record(
                db, request, ctx, "content.media_attached", "lesson_version", vid, entity.org, media_id=str(mid)
            )
    response.status_code = 201 if created else 200
    return {"lesson_version_id": vid, "items": await _attachments(db, vid)}


@router.delete(
    "/lesson-versions/{version_id}/media/{media_id}",
    status_code=204,
    summary="Détacher un média d'un brouillon (fonction contrôlée)",
)
async def detach_media(version_id: str, media_id: str, request: Request, ctx: SensitiveAuth, db: Db) -> Response:
    vid, mid = parse_uuid(version_id), parse_uuid(media_id)
    async with editorial_tx(db, ctx):
        entity = await load(db, ctx, "lesson-versions", vid, lock=True, what="Version de leçon")
        ensure_can(ctx, "content:create", entity.org)
        if entity.status != "draft":
            raise problem("not_draft")
        await _media_in_scope(db, ctx, mid)
        removed = (await db.execute(text("SELECT nl_media_detach(:lv, :m)"), {"lv": vid, "m": mid})).scalar()
        if not removed:
            raise not_found("Rattachement")
        await record(db, request, ctx, "content.media_detached", "lesson_version", vid, entity.org, media_id=str(mid))
    return Response(status_code=204)

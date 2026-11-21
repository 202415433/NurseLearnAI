"""Workflow éditorial (b3) : soumission, relecture indépendante, demandes de modifications,
approbation, archivage, abandon ; file de relecture, historique des décisions, comparaison.

L'API pré-vérifie (messages clairs, 403/409/422) ; les déclencheurs PostgreSQL revérifient tout
avec l'acteur déclaré par la session (`nl.actor_id`). Aucune décision n'est écrite par l'API : le
journal `editorial_decisions` est alimenté uniquement par ces déclencheurs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError
from app.domains.editorial import schemas as s
from app.domains.editorial.blocks import Body
from app.domains.editorial.diff import lesson_version_diff, module_version_diff, release_diff
from app.domains.editorial.errors import BASE, problem
from app.domains.editorial.routes_lessons import lesson_version_out, sources_of
from app.domains.editorial.routes_modules import module_version_items, module_version_out
from app.domains.editorial.routes_releases import release_items, release_out
from app.domains.editorial.scope import Entity, ensure_can, load, not_found, scope_params
from app.domains.editorial.tx import editorial_tx, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, parse_uuid
from app.domains.identity.sessions import AuthContext

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : relecture"])

KINDS = {"lesson-versions": "lesson_version", "module-versions": "module_version", "releases": "program_release"}
_WHAT = {"lesson-versions": "Version de leçon", "module-versions": "Version de module", "releases": "Édition"}
_SET_STATUS = {
    "lesson-versions": "UPDATE lesson_versions SET status = :to WHERE id = :id",
    "module-versions": "UPDATE module_versions SET status = :to WHERE id = :id",
    "releases": "UPDATE program_releases SET status = :to WHERE id = :id",
}
_APPROVE_LESSON = "UPDATE lesson_versions SET status = 'approved', next_review_due = :due WHERE id = :id"
MAX_REVIEW_HORIZON = dt.timedelta(days=3 * 366)


@dataclass(frozen=True)
class Rule:
    sources: frozenset[str]
    target: str
    permissions: tuple[str, ...]
    independent: bool
    audit: str


RULES = {
    "submit": Rule(frozenset({"draft"}), "medical_review", ("content:create",), False, "content.submitted"),
    "request-changes": Rule(
        frozenset({"medical_review"}), "draft", ("content:review",), True, "content.changes_requested"
    ),
    "approve": Rule(frozenset({"medical_review"}), "approved", ("content:review",), True, "content.approved"),
    "archive": Rule(
        frozenset({"approved", "published"}),
        "archived",
        ("content:review", "content:publish"),
        False,
        "content.archived",
    ),
    "abandon": Rule(frozenset({"draft"}), "archived", ("content:create",), False, "content.abandoned"),
}


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise not_found("Ressource")
    return kind


async def _check_lesson_submission(db: AsyncSession, entity: Entity) -> None:
    """Contenu `blocks/v1` revalidé ; procédure ou encadré de sécurité ⇒ au moins une source citée ;
    tout bloc « reference » renvoie à une source citée par la version."""
    try:
        body = Body.model_validate(entity.row["body"])
    except ValidationError as exc:
        raise ProblemError(
            422,
            "Contenu de leçon invalide",
            type_=BASE + "invalid-input",
            extra={"code": "invalid_content", "errors": len(exc.errors())},
        ) from None
    if not body.blocks:
        raise problem("empty_content")
    cited = {x["reference_id"] for x in await sources_of(db, entity.id)}
    if body.needs_source and not cited:
        raise problem("missing_source", detail="Procédure ou consigne de sécurité : au moins une source citée")
    if not body.reference_ids <= cited:
        raise problem("incoherent_reference", detail="Un bloc « référence » cite une source non rattachée")


async def _check_lesson_approval(db: AsyncSession, entity: Entity, due: dt.date | None) -> None:
    today = dt.date.today()
    if due is None or due <= today or due > today + MAX_REVIEW_HORIZON:
        raise problem("review_date_required", detail="Date de prochaine révision future (au plus 3 ans) obligatoire")
    sources = await sources_of(db, entity.id)
    if any(not x["verified"] or x["status"] != "active" for x in sources):
        raise problem("source_unverified", detail="Toutes les sources citées doivent être actives et vérifiées")
    body = Body.model_validate(entity.row["body"])
    if body.needs_source and not sources:
        raise problem("missing_source")


async def _transition(
    db: AsyncSession,
    request: Request,
    ctx: AuthContext,
    kind: str,
    entity_id: str,
    action: str,
    *,
    comment: str | None = None,
    due: dt.date | None = None,
) -> dict[str, Any]:
    kind = _kind(kind)
    rule = RULES[action]
    eid = parse_uuid(entity_id)
    async with editorial_tx(db, ctx, comment=comment):
        entity = await load(db, ctx, kind, eid, lock=True, what=_WHAT[kind])
        ensure_can(ctx, rule.permissions, entity.org)
        if entity.status not in rule.sources:
            raise problem("transition_forbidden", extra={"status": entity.status})
        if rule.independent and entity.author_id == ctx.user_id:
            raise problem("self_approval")
        if kind == "lesson-versions" and action == "submit":
            await _check_lesson_submission(db, entity)
        if kind == "lesson-versions" and action == "approve":
            await _check_lesson_approval(db, entity, due)
            await db.execute(text(_APPROVE_LESSON), {"id": entity.id, "due": due})
        else:
            await db.execute(text(_SET_STATUS[kind]), {"id": entity.id, "to": rule.target})
        decision = (
            (
                await db.execute(
                    text(
                        "SELECT id, decision, from_status, to_status, actor_id, organization_id, content_sha256, "
                        "decided_at FROM editorial_decisions WHERE entity_type = :t AND entity_id = :id "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"t": KINDS[kind], "id": entity.id},
                )
            )
            .mappings()
            .one()
        )
        await record(
            db,
            request,
            ctx,
            rule.audit,
            KINDS[kind],
            entity.id,
            entity.org,
            from_status=entity.status,
            to_status=rule.target,
            content_sha256=decision["content_sha256"],
            decision_id=decision["id"],
        )
    return {"id": entity.id, "kind": kind, "status": rule.target, "decision": dict(decision)}


@router.post("/{kind}/{entity_id}/submit", summary="Soumettre à la relecture médicale")
async def submit(kind: str, entity_id: str, request: Request, ctx: SensitiveAuth, db: Db) -> dict[str, Any]:
    return await _transition(db, request, ctx, kind, entity_id, "submit")


@router.post("/{kind}/{entity_id}/request-changes", summary="Renvoyer en brouillon avec un motif")
async def request_changes(
    kind: str, entity_id: str, body: s.RequestChangesIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    return await _transition(db, request, ctx, kind, entity_id, "request-changes", comment=body.comment)


@router.post("/{kind}/{entity_id}/approve", summary="Approuver (relecteur indépendant de l'auteur)")
async def approve(
    kind: str, entity_id: str, body: s.ApproveIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    if kind != "lesson-versions" and body.next_review_due is not None:
        raise ProblemError(
            422,
            "next_review_due concerne les versions de leçon",
            type_=BASE + "invalid-input",
            extra={"code": "invalid_field"},
        )
    return await _transition(db, request, ctx, kind, entity_id, "approve", due=body.next_review_due)


@router.post("/{kind}/{entity_id}/archive", summary="Archiver une version approuvée ou publiée")
async def archive(
    kind: str, entity_id: str, body: s.ReasonIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    return await _transition(db, request, ctx, kind, entity_id, "archive", comment=body.reason)


@router.post("/{kind}/{entity_id}/abandon", summary="Abandonner un brouillon (conservé dans l'historique)")
async def abandon(
    kind: str, entity_id: str, body: s.ReasonIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    return await _transition(db, request, ctx, kind, entity_id, "abandon", comment=body.reason)


_QUEUE = text(
    """
SELECT * FROM (
  SELECT 'lesson-versions' AS kind, lv.id, lv.title, m.organization_id, lv.author_id, lv.submitted_at
  FROM lesson_versions lv JOIN lessons l ON l.id = lv.lesson_id JOIN modules m ON m.id = l.module_id
  WHERE lv.status = 'medical_review'
  UNION ALL
  SELECT 'module-versions', mv.id, coalesce(m.title_i18n ->> 'fr', m.code) || ' — v' || mv.version_no,
         m.organization_id, mv.author_id, mv.submitted_at
  FROM module_versions mv JOIN modules m ON m.id = mv.module_id WHERE mv.status = 'medical_review'
  UNION ALL
  SELECT 'releases', r.id, coalesce(p.title_i18n ->> 'fr', p.code) || ' — édition ' || r.release_no,
         p.organization_id, r.author_id, r.submitted_at
  FROM program_releases r JOIN programs p ON p.id = r.program_id WHERE r.status = 'medical_review'
) q
WHERE (CAST(:scope_all AS boolean) OR q.organization_id = ANY(CAST(:scope_orgs AS uuid[])))
ORDER BY q.submitted_at NULLS LAST, q.id
LIMIT 500
"""
)


@router.get("/review-queue", summary="File de relecture (portée content:review)")
async def review_queue(ctx: Auth, db: Db) -> list[dict[str, Any]]:
    params = scope_params(ctx, ("content:review",))
    if not params["scope_all"] and not params["scope_orgs"]:
        raise problem("reviewer_required")
    rows = await db.execute(_QUEUE, params)
    return [{**dict(r), "can_review": r["author_id"] != ctx.user_id} for r in rows.mappings()]


@router.get("/{kind}/{entity_id}/decisions", summary="Historique des décisions éditoriales")
async def decisions(kind: str, entity_id: str, ctx: Auth, db: Db) -> list[dict[str, Any]]:
    kind = _kind(kind)
    entity = await load(db, ctx, kind, parse_uuid(entity_id), what=_WHAT[kind])
    rows = await db.execute(
        text(
            "SELECT id, decision, from_status, to_status, actor_id, organization_id, content_sha256, comment, "
            "decided_at FROM editorial_decisions WHERE entity_type = :t AND entity_id = :id ORDER BY id"
        ),
        {"t": KINDS[kind], "id": entity.id},
    )
    return [dict(r) for r in rows.mappings()]


async def _snapshot(db: AsyncSession, kind: str, entity: Entity) -> dict[str, Any]:
    if kind == "lesson-versions":
        return lesson_version_out(entity.row, await sources_of(db, entity.id))
    if kind == "module-versions":
        return module_version_out(entity.row, await module_version_items(db, entity.id))
    return release_out(entity.row, await release_items(db, entity.id))


@router.get("/{kind}/{entity_id}/diff", summary="Comparer deux versions d'un même élément")
async def diff(
    kind: str, entity_id: str, ctx: Auth, db: Db, against: str = Query(..., description="Version de référence")
) -> dict[str, Any]:
    kind = _kind(kind)
    new = await load(db, ctx, kind, parse_uuid(entity_id), what=_WHAT[kind])
    old = await load(db, ctx, kind, parse_uuid(against), what=_WHAT[kind])
    if new.parent_id != old.parent_id:
        raise ProblemError(
            422,
            "Versions d'éléments différents : comparaison impossible",
            type_=BASE + "invalid-input",
            extra={"code": "incomparable"},
        )
    a, b = await _snapshot(db, kind, old), await _snapshot(db, kind, new)
    if kind == "lesson-versions":
        changes = lesson_version_diff(a, b)
    elif kind == "module-versions":
        changes = module_version_diff(a, b)
    else:
        changes = release_diff(a, b)
    return {"kind": kind, "from": old.id, "to": new.id, "changes": changes}

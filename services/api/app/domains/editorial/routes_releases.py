"""Éditions de programme (D12) et publication contrôlée (b2, b5).

Une édition cite des versions de modules ; ses prérequis sont figés avec elle. La publication est
l'UNIQUE voie de mise en ligne : aucune route ne publie une leçon ou un module isolément. La
publication réelle d'un programme médical reste désactivée (`medical_publication_enabled`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemError
from app.domains.editorial import schemas as s
from app.domains.editorial.errors import BASE, problem
from app.domains.editorial.queries import ensure_publishable
from app.domains.editorial.scope import ensure_can, ensure_visible, load
from app.domains.editorial.tx import check_if_match, editorial_tx, etag_of, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, get_settings_dep, parse_uuid

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : éditions de programme"])
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


async def release_items(db: AsyncSession, release_id: Any) -> dict[str, Any]:
    modules = await db.execute(
        text(
            "SELECT rm.module_id, m.code AS module_code, rm.module_version_id, mv.version_no, mv.status, "
            "rm.position, rm.required FROM program_release_modules rm "
            "JOIN module_versions mv ON mv.id = rm.module_version_id JOIN modules m ON m.id = rm.module_id "
            "WHERE rm.release_id = :r ORDER BY rm.position"
        ),
        {"r": release_id},
    )
    prereqs = await db.execute(
        text(
            "SELECT module_id, prerequisite_module_id AS requires FROM program_release_prerequisites "
            "WHERE release_id = :r ORDER BY module_id, prerequisite_module_id"
        ),
        {"r": release_id},
    )
    return {"modules": [dict(r) for r in modules.mappings()], "prerequisites": [dict(r) for r in prereqs.mappings()]}


def release_etag(row: Any, items: dict[str, Any]) -> str:
    return etag_of({"status": row["status"], "change_summary": row["change_summary"], **items})


def release_out(row: Any, items: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "program_id": row["program_id"],
        "program_code": row["program_code"],
        "organization_id": row["org"],
        "is_medical": row["is_medical"],
        "release_no": row["release_no"],
        "structure": row["structure"],
        "status": row["status"],
        "change_summary": row["change_summary"],
        "author_id": row["author_id"],
        "submitted_at": row["submitted_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "published_by": row["published_by"],
        "published_at": row["published_at"],
        "archived_at": row["archived_at"],
        "content_sha256": row["content_sha256"],
        **items,
    }


async def _respond(db: AsyncSession, ctx: Any, release_id: Any, response: Response) -> dict[str, Any]:
    entity = await load(db, ctx, "releases", release_id, what="Édition")
    items = await release_items(db, entity.id)
    response.headers["ETag"] = release_etag(entity.row, items)
    return release_out(entity.row, items)


async def _write_items(db: AsyncSession, ctx: Any, release_id: Any, body: s.ReleaseIn) -> None:
    """Remplace les versions de modules et prérequis d'un brouillon d'édition.

    Chaque version citée doit être VISIBLE de l'utilisateur (sinon 404, sans révéler son existence) ;
    la base revérifie l'appartenance du module à l'établissement du programme et la cohérence.
    """
    module_of: dict[Any, Any] = {}
    for item in body.modules:
        mv = await load(db, ctx, "module-versions", item.module_version_id, what="Version de module")
        ensure_visible(ctx, mv.org, "Version de module")
        if mv.row["module_id"] in module_of.values():
            raise ProblemError(
                422,
                "Une seule version par module dans une édition",
                type_=BASE + "invalid-input",
                extra={"code": "duplicate_module"},
            )
        module_of[item.module_version_id] = mv.row["module_id"]
    included = set(module_of.values())
    for p in body.prerequisites:
        if p.module_id not in included or p.requires not in included:
            raise ProblemError(
                422, "Prérequis hors de l'édition", type_=BASE + "invalid-input", extra={"code": "prerequisite_scope"}
            )
    await db.execute(text("DELETE FROM program_release_modules WHERE release_id = :r"), {"r": release_id})
    for item in body.modules:
        await db.execute(
            text(
                "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position, required) "
                "VALUES (:r, :m, :mv, :pos, :req)"
            ),
            {
                "r": release_id,
                "m": module_of[item.module_version_id],
                "mv": item.module_version_id,
                "pos": item.position,
                "req": item.required,
            },
        )
    for p in body.prerequisites:
        await db.execute(
            text(
                "INSERT INTO program_release_prerequisites (release_id, module_id, prerequisite_module_id) "
                "VALUES (:r, :m, :p)"
            ),
            {"r": release_id, "m": p.module_id, "p": p.requires},
        )


@router.get("/programs/{program_id}/releases", summary="Éditions d'un programme")
async def list_releases(program_id: str, ctx: Auth, db: Db) -> list[dict[str, Any]]:
    program = await load(db, ctx, "program", parse_uuid(program_id), what="Programme")
    rows = await db.execute(
        text(
            "SELECT id, release_no, structure, status, author_id, submitted_at, reviewed_by, reviewed_at, "
            "published_at, archived_at FROM program_releases WHERE program_id = :p ORDER BY release_no"
        ),
        {"p": program.id},
    )
    return [dict(r) for r in rows.mappings()]


@router.post("/programs/{program_id}/releases", status_code=201, summary="Créer un brouillon d'édition")
async def create_release(
    program_id: str, body: s.ReleaseIn, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    program = await load(db, ctx, "program", parse_uuid(program_id), what="Programme")
    ensure_can(ctx, "content:create", program.org)
    async with editorial_tx(db, ctx):
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('nl_release_' || :p))"), {"p": str(program.id)})
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO program_releases (program_id, release_no, change_summary, author_id, structure) "
                        "SELECT :p, coalesce(max(release_no), 0) + 1, :summary, :author, 'module_versions' "
                        "FROM program_releases WHERE program_id = :p RETURNING id"
                    ),
                    {"p": program.id, "summary": body.change_summary, "author": ctx.user_id},
                )
            )
            .mappings()
            .one()
        )
        await _write_items(db, ctx, row["id"], body)
        await record(
            db, request, ctx, "release.created", "program_release", row["id"], program.org, modules=len(body.modules)
        )
    return await _respond(db, ctx, row["id"], response)


@router.get("/releases/{release_id}", summary="Édition (en-tête ETag)")
async def get_release(release_id: str, response: Response, ctx: Auth, db: Db) -> dict[str, Any]:
    return await _respond(db, ctx, parse_uuid(release_id), response)


@router.patch("/releases/{release_id}", summary="Remplacer le contenu d'un brouillon d'édition (If-Match)")
async def patch_release(
    release_id: str, body: s.ReleaseIn, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    rid = parse_uuid(release_id)
    async with editorial_tx(db, ctx):
        entity = await load(db, ctx, "releases", rid, lock=True, what="Édition")
        ensure_can(ctx, "content:create", entity.org)
        if entity.status != "draft":
            raise problem("not_draft")
        if entity.row["structure"] != "module_versions":
            raise problem("legacy_structure")
        check_if_match(request, release_etag(entity.row, await release_items(db, entity.id)))
        await db.execute(
            text("UPDATE program_releases SET change_summary = :summary WHERE id = :id"),
            {"id": entity.id, "summary": body.change_summary},
        )
        await _write_items(db, ctx, entity.id, body)
        await record(
            db, request, ctx, "content.updated", "program_release", entity.id, entity.org, modules=len(body.modules)
        )
    return await _respond(db, ctx, rid, response)


@router.post("/releases/{release_id}/publish", summary="Publier une édition approuvée (seule voie de publication)")
async def publish_release(
    release_id: str, request: Request, response: Response, ctx: SensitiveAuth, db: Db, settings: SettingsDep
) -> dict[str, Any]:
    rid = parse_uuid(release_id)
    async with editorial_tx(db, ctx):
        entity = await load(db, ctx, "releases", rid, lock=True, what="Édition")
        ensure_can(ctx, "content:publish", entity.org)
        if entity.status != "approved":
            raise problem("transition_forbidden", detail="Seule une édition approuvée peut être publiée")
        if entity.row["is_medical"] and not settings.medical_publication_enabled:
            # Garde de production : aucun contenu médical réel n'est mis en ligne tant que les
            # conditions (MFA des relecteurs, relecteur qualifié, textes juridiques) ne sont pas réunies.
            raise ProblemError(
                403,
                "Publication réelle d'un programme médical désactivée tant que les conditions de production "
                "ne sont pas réunies",
                type_=BASE + "medical-publication-disabled",
                extra={"code": "medical_publication_disabled"},
            )
        await ensure_publishable(db, entity.id)
        await db.execute(text("UPDATE program_releases SET status = 'published' WHERE id = :id"), {"id": entity.id})
        await record(
            db,
            request,
            ctx,
            "content.published",
            "program_release",
            entity.id,
            entity.org,
            program=entity.row["program_code"],
            release_no=entity.row["release_no"],
            is_medical=entity.row["is_medical"],
        )
    return await _respond(db, ctx, rid, response)

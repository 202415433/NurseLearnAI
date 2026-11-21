"""Versions de modules (D12) : instantané des leçons et des versions exactes par langue (b2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError
from app.domains.editorial import schemas as s
from app.domains.editorial.errors import BASE, problem
from app.domains.editorial.scope import ensure_can, load
from app.domains.editorial.tx import check_if_match, editorial_tx, etag_of, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, parse_uuid

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : versions de module"])


async def module_version_items(db: AsyncSession, version_id: Any) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "SELECT ml.lesson_id, l.code AS lesson_code, ml.position, ml.required, "
            "coalesce(jsonb_object_agg(x.locale, x.lesson_version_id) FILTER (WHERE x.locale IS NOT NULL), "
            "'{}'::jsonb) AS versions "
            "FROM module_version_lessons ml JOIN lessons l ON l.id = ml.lesson_id "
            "LEFT JOIN module_version_lesson_versions x "
            "ON x.module_version_id = ml.module_version_id AND x.lesson_id = ml.lesson_id "
            "WHERE ml.module_version_id = :v GROUP BY ml.lesson_id, l.code, ml.position, ml.required "
            "ORDER BY ml.position"
        ),
        {"v": version_id},
    )
    return [dict(r) for r in rows.mappings()]


def module_version_etag(row: Any, items: list[dict[str, Any]]) -> str:
    return etag_of({"status": row["status"], "change_summary": row["change_summary"], "lessons": items})


def module_version_out(row: Any, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "module_id": row["module_id"],
        "module_code": row["module_code"],
        "organization_id": row["org"],
        "version_no": row["version_no"],
        "status": row["status"],
        "change_summary": row["change_summary"],
        "author_id": row["author_id"],
        "submitted_at": row["submitted_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "published_at": row["published_at"],
        "archived_at": row["archived_at"],
        "content_sha256": row["content_sha256"],
        "lessons": items,
    }


async def _respond(db: AsyncSession, ctx: Any, version_id: Any, response: Response) -> dict[str, Any]:
    entity = await load(db, ctx, "module-versions", version_id, what="Version de module")
    items = await module_version_items(db, entity.id)
    response.headers["ETag"] = module_version_etag(entity.row, items)
    return module_version_out(entity.row, items)


async def _write_items(db: AsyncSession, version_id: Any, module_id: Any, lessons: list[s.ModuleVersionLesson]) -> None:
    await db.execute(text("DELETE FROM module_version_lessons WHERE module_version_id = :v"), {"v": version_id})
    for item in lessons:
        await db.execute(
            text(
                "INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position, required) "
                "VALUES (:v, :m, :l, :pos, :req)"
            ),
            {"v": version_id, "m": module_id, "l": item.lesson_id, "pos": item.position, "req": item.required},
        )
        for locale, lesson_version_id in sorted(item.versions.items()):
            await db.execute(
                text(
                    "INSERT INTO module_version_lesson_versions (module_version_id, lesson_id, locale, "
                    "lesson_version_id) VALUES (:v, :l, :loc, :lv)"
                ),
                {"v": version_id, "l": item.lesson_id, "loc": locale, "lv": lesson_version_id},
            )


@router.post("/modules/{module_id}/versions", status_code=201, summary="Créer un brouillon de version de module")
async def create_module_version(
    module_id: str, body: s.ModuleVersionIn, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    module = await load(db, ctx, "module", parse_uuid(module_id), what="Module")
    ensure_can(ctx, "content:create", module.org)
    async with editorial_tx(db, ctx):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('nl_module_version_' || :m))"), {"m": str(module.id)}
        )
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO module_versions (module_id, version_no, change_summary, author_id) "
                        "SELECT :m, coalesce(max(version_no), 0) + 1, :summary, :author FROM module_versions "
                        "WHERE module_id = :m RETURNING id"
                    ),
                    {"m": module.id, "summary": body.change_summary, "author": ctx.user_id},
                )
            )
            .mappings()
            .one()
        )
        if body.from_version_id is not None:
            exists = (
                await db.execute(
                    text("SELECT 1 FROM module_versions WHERE id = :id AND module_id = :m"),
                    {"id": body.from_version_id, "m": module.id},
                )
            ).first()
            if exists is None:
                raise ProblemError(404, "Version source introuvable pour ce module", type_=BASE + "not-found")
            await db.execute(
                text(
                    "INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position, required) "
                    "SELECT :new, module_id, lesson_id, position, required FROM module_version_lessons "
                    "WHERE module_version_id = :old"
                ),
                {"new": row["id"], "old": body.from_version_id},
            )
            await db.execute(
                text(
                    "INSERT INTO module_version_lesson_versions (module_version_id, lesson_id, locale, "
                    "lesson_version_id) SELECT :new, lesson_id, locale, lesson_version_id "
                    "FROM module_version_lesson_versions WHERE module_version_id = :old"
                ),
                {"new": row["id"], "old": body.from_version_id},
            )
        else:
            await _write_items(db, row["id"], module.id, body.lessons or [])
        await record(
            db,
            request,
            ctx,
            "content.created",
            "module_version",
            row["id"],
            module.org,
            from_version=str(body.from_version_id) if body.from_version_id else None,
        )
    return await _respond(db, ctx, row["id"], response)


@router.get("/module-versions/{version_id}", summary="Version de module (en-tête ETag)")
async def get_module_version(version_id: str, response: Response, ctx: Auth, db: Db) -> dict[str, Any]:
    return await _respond(db, ctx, parse_uuid(version_id), response)


@router.patch("/module-versions/{version_id}", summary="Remplacer le contenu d'un brouillon (If-Match obligatoire)")
async def patch_module_version(
    version_id: str, body: s.ModuleVersionPatch, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    vid = parse_uuid(version_id)
    async with editorial_tx(db, ctx):
        entity = await load(db, ctx, "module-versions", vid, lock=True, what="Version de module")
        ensure_can(ctx, "content:create", entity.org)
        if entity.status != "draft":
            raise problem("not_draft")
        check_if_match(request, module_version_etag(entity.row, await module_version_items(db, entity.id)))
        if "change_summary" in body.model_fields_set:
            await db.execute(
                text("UPDATE module_versions SET change_summary = :summary WHERE id = :id"),
                {"id": entity.id, "summary": body.change_summary},
            )
        await _write_items(db, entity.id, entity.row["module_id"], body.lessons)
        await record(
            db, request, ctx, "content.updated", "module_version", entity.id, entity.org, lessons=len(body.lessons)
        )
    return await _respond(db, ctx, vid, response)


@router.delete("/module-versions/{version_id}", status_code=204, summary="Supprimer un brouillon jamais soumis")
async def delete_module_version(version_id: str, request: Request, ctx: SensitiveAuth, db: Db) -> Response:
    vid = parse_uuid(version_id)
    async with editorial_tx(db, ctx):
        entity = await load(db, ctx, "module-versions", vid, lock=True, what="Version de module")
        ensure_can(ctx, "content:create", entity.org)
        if entity.status != "draft":
            raise problem("not_draft")
        check_if_match(request, module_version_etag(entity.row, await module_version_items(db, entity.id)))
        history = (
            await db.execute(
                text(
                    "SELECT 1 FROM editorial_decisions WHERE entity_type = 'module_version' AND entity_id = :id LIMIT 1"
                ),
                {"id": entity.id},
            )
        ).first()
        if history is not None:
            raise problem("history")
        cited = (
            await db.execute(
                text("SELECT 1 FROM program_release_modules WHERE module_version_id = :id LIMIT 1"), {"id": entity.id}
            )
        ).first()
        if cited is not None:
            raise problem("in_use", detail="Version citée par une édition : retirez-la d'abord")
        await db.execute(text("DELETE FROM module_versions WHERE id = :id"), {"id": entity.id})
        await record(db, request, ctx, "content.deleted", "module_version", entity.id, entity.org)
    return Response(status_code=204)

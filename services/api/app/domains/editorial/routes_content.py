"""Structure de travail : programmes, modules et leçons.

Ces lignes ne portent pas de contenu pédagogique (titres et métadonnées seulement) ; leur état
catalogue (`published`) n'est jamais modifiable ici : il découle d'une édition publiée (0005/0006).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.domains.editorial import schemas as s
from app.domains.editorial.scope import ensure_can, ensure_can_create, load, scope_params
from app.domains.editorial.tx import editorial_tx, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, parse_uuid

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : structure"])


def _js(value: Any) -> str | None:
    return json.dumps(value.as_json()) if value is not None else None


def program_out(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "profession_id": row["profession_id"],
        "specialty_id": row["specialty_id"],
        "organization_id": row["organization_id"],
        "title": row["title_i18n"],
        "summary": row["summary_i18n"],
        "level": row["level"],
        "access_tier": row["access_tier"],
        "status": row["status"],
        "is_medical": row["is_medical"],
        "updated_at": row["updated_at"],
    }


def module_out(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "specialty_id": row["specialty_id"],
        "organization_id": row["organization_id"],
        "title": row["title_i18n"],
        "summary": row["summary_i18n"],
        "level": row["level"],
        "estimated_minutes": row["estimated_minutes"],
        "access_tier": row["access_tier"],
        "status": row["status"],
        "updated_at": row["updated_at"],
    }


def lesson_out(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "module_id": row["module_id"],
        "code": row["code"],
        "position": row["position"],
        "kind": row["kind"],
        "estimated_minutes": row["estimated_minutes"],
    }


# ---------------------------------------------------------------------------- programmes
@router.get("/programs", summary="Programmes (brouillons compris) dans la portée")
async def list_programs(ctx: Auth, db: Db) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "SELECT p.* FROM programs p WHERE (CAST(:scope_all AS boolean) "
            "OR p.organization_id = ANY(CAST(:scope_orgs AS uuid[]))) ORDER BY p.code LIMIT 500"
        ),
        scope_params(ctx),
    )
    return [program_out(r) for r in rows.mappings()]


@router.get("/programs/{program_id}", summary="Programme")
async def get_program(program_id: str, ctx: Auth, db: Db) -> dict[str, Any]:
    entity = await load(db, ctx, "program", parse_uuid(program_id), what="Programme")
    return program_out(entity.row)


@router.post("/programs", status_code=201, summary="Créer un programme (brouillon)")
async def create_program(body: s.ProgramIn, request: Request, ctx: SensitiveAuth, db: Db) -> dict[str, Any]:
    ensure_can_create(ctx, body.organization_id)
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO programs (code, profession_id, specialty_id, organization_id, title_i18n, "
                        "summary_i18n, level, access_tier, is_medical) VALUES (:code, :prof, :spec, :org, "
                        "CAST(:title AS jsonb), CAST(:summary AS jsonb), :level, :tier, :medical) RETURNING *"
                    ),
                    {
                        "code": body.code,
                        "prof": body.profession_id,
                        "spec": body.specialty_id,
                        "org": body.organization_id,
                        "title": _js(body.title),
                        "summary": _js(body.summary),
                        "level": body.level,
                        "tier": body.access_tier,
                        "medical": body.is_medical,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "content.created", "program", row["id"], row["organization_id"])
    return program_out(row)


@router.patch("/programs/{program_id}", summary="Modifier un programme (hors état)")
async def patch_program(
    program_id: str, body: s.ProgramPatch, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    entity = await load(db, ctx, "program", parse_uuid(program_id), what="Programme")
    ensure_can(ctx, "content:create", entity.org)
    fields = sorted(body.model_dump(exclude_unset=True, exclude_none=True))
    if not fields:
        return program_out(entity.row)
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "UPDATE programs SET title_i18n = coalesce(CAST(:title AS jsonb), title_i18n), "
                        "summary_i18n = coalesce(CAST(:summary AS jsonb), summary_i18n), "
                        "level = coalesce(:level, level), specialty_id = coalesce(:spec, specialty_id), "
                        "is_medical = coalesce(:medical, is_medical), updated_at = now() "
                        "WHERE id = :id RETURNING *"
                    ),
                    {
                        "id": entity.id,
                        "title": _js(body.title),
                        "summary": _js(body.summary),
                        "level": body.level,
                        "spec": body.specialty_id,
                        "medical": body.is_medical,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "content.updated", "program", entity.id, entity.org, fields=fields)
    return program_out(row)


# ---------------------------------------------------------------------------- modules
@router.get("/modules", summary="Modules (brouillons compris) dans la portée")
async def list_modules(ctx: Auth, db: Db) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "SELECT m.* FROM modules m WHERE (CAST(:scope_all AS boolean) "
            "OR m.organization_id = ANY(CAST(:scope_orgs AS uuid[]))) ORDER BY m.code LIMIT 500"
        ),
        scope_params(ctx),
    )
    return [module_out(r) for r in rows.mappings()]


@router.get("/modules/{module_id}", summary="Module, leçons et versions de module")
async def get_module(module_id: str, ctx: Auth, db: Db) -> dict[str, Any]:
    entity = await load(db, ctx, "module", parse_uuid(module_id), what="Module")
    lessons = await db.execute(text("SELECT * FROM lessons WHERE module_id = :m ORDER BY position"), {"m": entity.id})
    versions = await db.execute(
        text(
            "SELECT id, version_no, status, author_id, submitted_at, reviewed_by, reviewed_at, published_at "
            "FROM module_versions WHERE module_id = :m ORDER BY version_no"
        ),
        {"m": entity.id},
    )
    return {
        **module_out(entity.row),
        "lessons": [lesson_out(r) for r in lessons.mappings()],
        "versions": [dict(v) for v in versions.mappings()],
    }


@router.post("/modules", status_code=201, summary="Créer un module (brouillon)")
async def create_module(body: s.ModuleIn, request: Request, ctx: SensitiveAuth, db: Db) -> dict[str, Any]:
    ensure_can_create(ctx, body.organization_id)
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO modules (code, specialty_id, organization_id, title_i18n, summary_i18n, "
                        "level, estimated_minutes, access_tier, created_by) VALUES (:code, :spec, :org, "
                        "CAST(:title AS jsonb), CAST(:summary AS jsonb), :level, :minutes, :tier, :by) RETURNING *"
                    ),
                    {
                        "code": body.code,
                        "spec": body.specialty_id,
                        "org": body.organization_id,
                        "title": _js(body.title),
                        "summary": _js(body.summary),
                        "level": body.level,
                        "minutes": body.estimated_minutes,
                        "tier": body.access_tier,
                        "by": ctx.user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "content.created", "module", row["id"], row["organization_id"])
    return module_out(row)


@router.patch("/modules/{module_id}", summary="Modifier un module (hors état)")
async def patch_module(
    module_id: str, body: s.ModulePatch, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    entity = await load(db, ctx, "module", parse_uuid(module_id), what="Module")
    ensure_can(ctx, "content:create", entity.org)
    fields = sorted(body.model_dump(exclude_unset=True, exclude_none=True))
    if not fields:
        return module_out(entity.row)
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "UPDATE modules SET title_i18n = coalesce(CAST(:title AS jsonb), title_i18n), "
                        "summary_i18n = coalesce(CAST(:summary AS jsonb), summary_i18n), "
                        "level = coalesce(:level, level), estimated_minutes = coalesce(:minutes, estimated_minutes), "
                        "updated_at = now() WHERE id = :id RETURNING *"
                    ),
                    {
                        "id": entity.id,
                        "title": _js(body.title),
                        "summary": _js(body.summary),
                        "level": body.level,
                        "minutes": body.estimated_minutes,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "content.updated", "module", entity.id, entity.org, fields=fields)
    return module_out(row)


# ---------------------------------------------------------------------------- leçons
@router.post("/modules/{module_id}/lessons", status_code=201, summary="Ajouter une leçon au module")
async def create_lesson(
    module_id: str, body: s.LessonIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    entity = await load(db, ctx, "module", parse_uuid(module_id), what="Module")
    ensure_can(ctx, "content:create", entity.org)
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO lessons (module_id, code, position, kind, estimated_minutes) "
                        "VALUES (:m, :code, :pos, :kind, :minutes) RETURNING *"
                    ),
                    {
                        "m": entity.id,
                        "code": body.code,
                        "pos": body.position,
                        "kind": body.kind,
                        "minutes": body.estimated_minutes,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "content.created", "lesson", row["id"], entity.org)
    return lesson_out(row)


@router.get("/lessons/{lesson_id}", summary="Leçon et ses versions")
async def get_lesson(lesson_id: str, ctx: Auth, db: Db) -> dict[str, Any]:
    entity = await load(db, ctx, "lesson", parse_uuid(lesson_id), what="Leçon")
    versions = await db.execute(
        text(
            "SELECT id, locale, jurisdiction_id, version_no, status, author_id, submitted_at, reviewed_by, "
            "reviewed_at, published_at FROM lesson_versions WHERE lesson_id = :l ORDER BY locale, version_no"
        ),
        {"l": entity.id},
    )
    return {**lesson_out(entity.row), "versions": [dict(v) for v in versions.mappings()]}


@router.patch("/lessons/{lesson_id}", summary="Modifier une leçon (structure de travail)")
async def patch_lesson(
    lesson_id: str, body: s.LessonPatch, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    entity = await load(db, ctx, "lesson", parse_uuid(lesson_id), what="Leçon")
    ensure_can(ctx, "content:create", entity.org)
    fields = sorted(body.model_dump(exclude_unset=True, exclude_none=True))
    if not fields:
        return lesson_out(entity.row)
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "UPDATE lessons SET position = coalesce(:pos, position), kind = coalesce(:kind, kind), "
                        "estimated_minutes = coalesce(:minutes, estimated_minutes), updated_at = now() "
                        "WHERE id = :id RETURNING *"
                    ),
                    {"id": entity.id, "pos": body.position, "kind": body.kind, "minutes": body.estimated_minutes},
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "content.updated", "lesson", entity.id, entity.org, fields=fields)
    return lesson_out(row)

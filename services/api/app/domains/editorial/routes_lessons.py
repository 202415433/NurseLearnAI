"""Versions de leçon : brouillons, ETag / If-Match, sources citées (étape 2.2.2-b, b2)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError
from app.domains.editorial import schemas as s
from app.domains.editorial.errors import BASE, problem
from app.domains.editorial.queries import ensure_deletable
from app.domains.editorial.scope import Entity, ensure_can, load
from app.domains.editorial.tx import check_if_match, editorial_tx, etag_of, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, parse_uuid

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : versions de leçon"])

EMPTY_BODY = {"schema": "blocks/v1", "blocks": []}


async def sources_of(db: AsyncSession, version_id: Any) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "SELECT r.reference_id, r.locator, mr.title, mr.status, mr.last_verified_at, "
            "(mr.verified_by IS NOT NULL AND mr.last_verified_at IS NOT NULL) AS verified "
            "FROM lesson_version_references r JOIN medical_references mr ON mr.id = r.reference_id "
            "WHERE r.lesson_version_id = :v ORDER BY mr.title, r.reference_id"
        ),
        {"v": version_id},
    )
    return [dict(r) for r in rows.mappings()]


def lesson_version_etag(row: Any, sources: list[dict[str, Any]]) -> str:
    return etag_of(
        {
            "status": row["status"],
            "title": row["title"],
            "body": row["body"],
            "change_summary": row["change_summary"],
            "sources": [[str(x["reference_id"]), x["locator"]] for x in sources],
        }
    )


def lesson_version_out(row: Any, sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "lesson_id": row["lesson_id"],
        "module_id": row["module_id"],
        "organization_id": row["org"],
        "locale": row["locale"],
        "jurisdiction_id": row["jurisdiction_id"],
        "version_no": row["version_no"],
        "status": row["status"],
        "title": row["title"],
        "body": row["body"],
        "change_summary": row["change_summary"],
        "author_id": row["author_id"],
        "submitted_at": row["submitted_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "next_review_due": row["next_review_due"],
        "published_at": row["published_at"],
        "content_sha256": row["content_sha256"],
        "sources": sources,
    }


async def _respond(db: AsyncSession, ctx: Any, version_id: Any, response: Response) -> dict[str, Any]:
    entity = await load(db, ctx, "lesson-versions", version_id, what="Version de leçon")
    sources = await sources_of(db, entity.id)
    response.headers["ETag"] = lesson_version_etag(entity.row, sources)
    return lesson_version_out(entity.row, sources)


async def _locked_draft(db: AsyncSession, ctx: Any, request: Request, version_id: Any) -> Entity:
    """Verrouille la version, vérifie la permission, l'état brouillon et l'ETag (If-Match)."""
    entity = await load(db, ctx, "lesson-versions", version_id, lock=True, what="Version de leçon")
    ensure_can(ctx, "content:create", entity.org)
    if entity.status != "draft":
        raise problem("not_draft")
    check_if_match(request, lesson_version_etag(entity.row, await sources_of(db, entity.id)))
    return entity


@router.post("/lessons/{lesson_id}/versions", status_code=201, summary="Créer un brouillon de version de leçon")
async def create_lesson_version(
    lesson_id: str, body: s.LessonVersionCreate, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    lesson = await load(db, ctx, "lesson", parse_uuid(lesson_id), what="Leçon")
    ensure_can(ctx, "content:create", lesson.org)
    async with editorial_tx(db, ctx):
        if body.from_version_id is not None:
            source = (
                (
                    await db.execute(
                        text("SELECT * FROM lesson_versions WHERE id = :id AND lesson_id = :l"),
                        {"id": body.from_version_id, "l": lesson.id},
                    )
                )
                .mappings()
                .first()
            )
            if source is None:
                raise ProblemError(404, "Version source introuvable pour cette leçon", type_=BASE + "not-found")
            locale, jurisdiction = source["locale"], source["jurisdiction_id"]
            title = body.title or source["title"]
            content = body.body.as_json() if body.body else source["body"]
            summary = body.change_summary
        else:
            locale, jurisdiction = body.locale, body.jurisdiction_id
            title = body.title
            content = body.body.as_json() if body.body else EMPTY_BODY
            summary = body.change_summary
        # Numérotation sérialisée par leçon × langue × juridiction (pas de course entre deux créations).
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('nl_lesson_version_' || :l || ':' || :loc || ':' || :j))"),
            {"l": str(lesson.id), "loc": locale, "j": str(jurisdiction or "-")},
        )
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO lesson_versions (lesson_id, locale, jurisdiction_id, version_no, title, body, "
                        "change_summary, author_id) SELECT :l, :loc, :j, coalesce(max(version_no), 0) + 1, :title, "
                        "CAST(:body AS jsonb), :summary, :author FROM lesson_versions "
                        "WHERE lesson_id = :l AND locale = :loc AND jurisdiction_id IS NOT DISTINCT FROM :j "
                        "RETURNING id"
                    ),
                    {
                        "l": lesson.id,
                        "loc": locale,
                        "j": jurisdiction,
                        "title": title,
                        "body": json.dumps(content),
                        "summary": summary,
                        "author": ctx.user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        if body.from_version_id is not None:
            await db.execute(
                text(
                    "INSERT INTO lesson_version_references (lesson_version_id, reference_id, locator) "
                    "SELECT :new, reference_id, locator FROM lesson_version_references WHERE lesson_version_id = :old"
                ),
                {"new": row["id"], "old": body.from_version_id},
            )
        await record(
            db,
            request,
            ctx,
            "content.created",
            "lesson_version",
            row["id"],
            lesson.org,
            locale=locale,
            from_version=str(body.from_version_id) if body.from_version_id else None,
        )
    return await _respond(db, ctx, row["id"], response)


@router.get("/lesson-versions/{version_id}", summary="Version de leçon (en-tête ETag)")
async def get_lesson_version(version_id: str, response: Response, ctx: Auth, db: Db) -> dict[str, Any]:
    return await _respond(db, ctx, parse_uuid(version_id), response)


@router.patch("/lesson-versions/{version_id}", summary="Modifier un brouillon (If-Match obligatoire)")
async def patch_lesson_version(
    version_id: str, body: s.LessonVersionPatch, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    vid = parse_uuid(version_id)
    fields = sorted(body.model_dump(exclude_unset=True, exclude_none=True))
    async with editorial_tx(db, ctx):
        entity = await _locked_draft(db, ctx, request, vid)
        if fields:
            await db.execute(
                text(
                    "UPDATE lesson_versions SET title = coalesce(:title, title), "
                    "body = coalesce(CAST(:body AS jsonb), body), "
                    "change_summary = coalesce(:summary, change_summary) WHERE id = :id"
                ),
                {
                    "id": entity.id,
                    "title": body.title,
                    "body": json.dumps(body.body.as_json()) if body.body else None,
                    "summary": body.change_summary,
                },
            )
            await record(db, request, ctx, "content.updated", "lesson_version", entity.id, entity.org, fields=fields)
    return await _respond(db, ctx, vid, response)


@router.put("/lesson-versions/{version_id}/sources", summary="Remplacer les sources citées (If-Match obligatoire)")
async def put_lesson_version_sources(
    version_id: str, body: s.SourcesPut, request: Request, response: Response, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    vid = parse_uuid(version_id)
    async with editorial_tx(db, ctx):
        entity = await _locked_draft(db, ctx, request, vid)
        ids = [r.reference_id for r in body.references]
        if ids:
            found = (
                await db.execute(
                    text("SELECT id, status FROM medical_references WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": [str(i) for i in ids]},
                )
            ).all()
            if len(found) != len(set(ids)):
                raise ProblemError(422, "Source inconnue", type_=BASE + "invalid-reference", extra={"code": "23503"})
            if any(status == "withdrawn" for _, status in found):
                raise problem("source_unverified", detail="Une source retirée ne peut pas être citée")
        await db.execute(text("DELETE FROM lesson_version_references WHERE lesson_version_id = :v"), {"v": entity.id})
        for ref in body.references:
            await db.execute(
                text(
                    "INSERT INTO lesson_version_references (lesson_version_id, reference_id, locator) "
                    "VALUES (:v, :r, :loc)"
                ),
                {"v": entity.id, "r": ref.reference_id, "loc": ref.locator},
            )
        await record(db, request, ctx, "content.updated", "lesson_version", entity.id, entity.org, sources=len(ids))
    return await _respond(db, ctx, vid, response)


@router.delete("/lesson-versions/{version_id}", status_code=204, summary="Supprimer un brouillon jamais soumis")
async def delete_lesson_version(version_id: str, request: Request, ctx: SensitiveAuth, db: Db) -> Response:
    vid = parse_uuid(version_id)
    async with editorial_tx(db, ctx):
        entity = await _locked_draft(db, ctx, request, vid)
        # Un brouillon déjà soumis puis renvoyé garde son historique : l'abandonner (archivage).
        await ensure_deletable(db, "lesson_version", entity.id)
        await db.execute(text("DELETE FROM lesson_version_references WHERE lesson_version_id = :v"), {"v": entity.id})
        await db.execute(text("DELETE FROM lesson_versions WHERE id = :id"), {"id": entity.id})
        await record(db, request, ctx, "content.deleted", "lesson_version", entity.id, entity.org)
    return Response(status_code=204)

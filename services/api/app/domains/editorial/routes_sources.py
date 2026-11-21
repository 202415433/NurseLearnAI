"""Sources médicales (b4) : enregistrement, vérification par un relecteur habilité, retrait.

Une source naît NON vérifiée. Seul un relecteur détenant `content:review` au niveau plateforme peut
la vérifier : la base pose alors `verified_by` depuis l'acteur de la session (0006) — aucune valeur
du client. Une source non vérifiée ou retirée est refusée lors de l'approbation d'une leçon et de la
publication d'un programme médical.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from app.domains.editorial import schemas as s
from app.domains.editorial.errors import problem
from app.domains.editorial.scope import READ_PERMS, not_found
from app.domains.editorial.tx import editorial_tx, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, parse_uuid
from app.domains.identity.sessions import AuthContext

router = APIRouter(prefix="/api/v1/editorial", tags=["édition : sources médicales"])

_COLUMNS = (
    "id, kind, title, authors, publisher, publication_year, url, doi, isbn, jurisdiction_id, status, "
    "superseded_by, last_verified_at, verified_by, created_at, updated_at"
)
_SELECT_ONE = text(f"SELECT {_COLUMNS} FROM medical_references WHERE id = :id")  # noqa: S608 - colonnes fixes


def _any_content_role(ctx: AuthContext) -> bool:
    return any(p in ctx.global_permissions for p in READ_PERMS) or any(
        granted.intersection(READ_PERMS) for granted in ctx.org_permissions.values()
    )


def _can_create(ctx: AuthContext) -> bool:
    return "content:create" in ctx.global_permissions or any(
        "content:create" in granted for granted in ctx.org_permissions.values()
    )


def _out(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["verified"] = row["verified_by"] is not None and row["last_verified_at"] is not None
    return data


@router.get("/references", summary="Sources médicales")
async def list_references(
    ctx: Auth,
    db: Db,
    status: str | None = Query(None, pattern="^(active|superseded|withdrawn)$"),
    verified: bool | None = None,
    q: str | None = Query(None, min_length=2, max_length=100),
) -> list[dict[str, Any]]:
    if not _any_content_role(ctx):
        raise problem("forbidden")
    rows = await db.execute(
        text(
            f"SELECT {_COLUMNS} FROM medical_references "  # noqa: S608 - colonnes fixes, filtres paramétrés
            "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
            "AND (CAST(:verified AS boolean) IS NULL OR "
            "(verified_by IS NOT NULL AND last_verified_at IS NOT NULL) = :verified) "
            "AND (CAST(:q AS text) IS NULL OR title ILIKE '%' || :q || '%' OR doi = :q) "
            "ORDER BY title, id LIMIT 500"
        ),
        {"status": status, "verified": verified, "q": q},
    )
    return [_out(r) for r in rows.mappings()]


@router.post("/references", status_code=201, summary="Enregistrer une source (non vérifiée)")
async def create_reference(body: s.ReferenceIn, request: Request, ctx: SensitiveAuth, db: Db) -> dict[str, Any]:
    if not _can_create(ctx):
        raise problem("forbidden")
    async with editorial_tx(db, ctx):
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO medical_references (kind, title, authors, publisher, publication_year, url, "
                        "doi, isbn, jurisdiction_id) VALUES (:kind, :title, :authors, :publisher, :year, :url, "
                        "CAST(:doi AS text), :isbn, :jur) RETURNING id"
                    ),
                    {
                        "kind": body.kind,
                        "title": body.title,
                        "authors": body.authors,
                        "publisher": body.publisher,
                        "year": body.publication_year,
                        "url": body.url,
                        "doi": body.doi,
                        "isbn": body.isbn,
                        "jur": body.jurisdiction_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        await record(db, request, ctx, "reference.created", "medical_reference", row["id"], None, kind=body.kind)
        out = (await db.execute(_SELECT_ONE, {"id": row["id"]})).mappings().one()
    return _out(out)


async def _locked_reference(db: Db, ctx: AuthContext, reference_id: str) -> Any:
    if not _any_content_role(ctx):
        raise not_found("Source")
    row = (
        (
            await db.execute(
                text("SELECT id, status FROM medical_references WHERE id = :id FOR UPDATE"),
                {"id": parse_uuid(reference_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise not_found("Source")
    if "content:review" not in ctx.global_permissions:
        raise problem("reviewer_required")
    return row


@router.post("/references/{reference_id}/verify", summary="Vérifier une source (relecteur habilité)")
async def verify_reference(reference_id: str, request: Request, ctx: SensitiveAuth, db: Db) -> dict[str, Any]:
    async with editorial_tx(db, ctx):
        row = await _locked_reference(db, ctx, reference_id)
        if row["status"] != "active":
            raise problem("transition_forbidden", detail="Seule une source active peut être vérifiée")
        await db.execute(
            text("UPDATE medical_references SET last_verified_at = current_date, updated_at = now() WHERE id = :id"),
            {"id": row["id"]},
        )
        await record(db, request, ctx, "reference.verified", "medical_reference", row["id"], None)
        out = (await db.execute(_SELECT_ONE, {"id": row["id"]})).mappings().one()
    return _out(out)


@router.post("/references/{reference_id}/withdraw", summary="Retirer une source (relecteur habilité)")
async def withdraw_reference(
    reference_id: str, body: s.ReasonIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    async with editorial_tx(db, ctx):
        row = await _locked_reference(db, ctx, reference_id)
        if row["status"] == "withdrawn":
            raise problem("transition_forbidden", detail="Source déjà retirée")
        await db.execute(
            text("UPDATE medical_references SET status = 'withdrawn', updated_at = now() WHERE id = :id"),
            {"id": row["id"]},
        )
        # Le motif n'est pas journalisé dans l'audit (texte libre) : seule sa longueur l'est.
        await record(
            db,
            request,
            ctx,
            "reference.withdrawn",
            "medical_reference",
            row["id"],
            None,
            reason_length=len(body.reason),
        )
        out = (await db.execute(_SELECT_ONE, {"id": row["id"]})).mappings().one()
    return _out(out)

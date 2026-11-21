"""Portée et visibilité des contenus éditoriaux (isolation des établissements).

Règle : un contenu appartient à un établissement (celui du module ou du programme) ou au catalogue
public (NULL). Il est VISIBLE pour qui détient `content:create`, `content:review` ou
`content:publish` dans cette portée (rôle global, ou rôle dans l'établissement) ; sinon 404, sans
révéler son existence. Une action non permise sur un contenu visible répond 403.
Ces règles reproduisent `nl_user_has_permission` (0003), que la base revérifie à chaque transition.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError
from app.domains.editorial.errors import BASE, problem
from app.domains.identity.sessions import AuthContext

READ_PERMS = ("content:create", "content:review", "content:publish")


def not_found(what: str = "Contenu") -> ProblemError:
    return ProblemError(404, f"{what} introuvable", type_=BASE + "not-found")


def can_read(ctx: AuthContext, org: uuid.UUID | None) -> bool:
    return any(ctx.can(p, org) for p in READ_PERMS)


def ensure_visible(ctx: AuthContext, org: uuid.UUID | None, what: str = "Contenu") -> None:
    if not can_read(ctx, org):
        raise not_found(what)


def ensure_can(
    ctx: AuthContext, permission: str | tuple[str, ...], org: uuid.UUID | None, what: str = "Contenu"
) -> None:
    ensure_visible(ctx, org, what)
    perms = (permission,) if isinstance(permission, str) else permission
    if not any(ctx.can(p, org) for p in perms):
        raise problem("forbidden")


def ensure_can_create(ctx: AuthContext, org: uuid.UUID | None) -> None:
    """Création dans une portée : catalogue public (org NULL) ⇒ permission globale, sinon 403 ;
    établissement ⇒ 404 si l'utilisateur n'y a aucun rôle (existence non révélée), 403 sinon."""
    if ctx.can("content:create", org):
        return
    if org is not None and not ctx.member_of(org) and not can_read(ctx, org):
        raise not_found("Établissement")
    raise problem("forbidden")


def scope_params(ctx: AuthContext, perms: tuple[str, ...] = READ_PERMS) -> dict[str, Any]:
    """Paramètres de portée d'une liste (aucune valeur du client). À combiner avec le fragment SQL
    statique `(CAST(:scope_all AS boolean) OR <colonne> = ANY(CAST(:scope_orgs AS uuid[])))`."""
    if any(p in ctx.global_permissions for p in perms):
        return {"scope_all": True, "scope_orgs": []}
    orgs = [str(o) for o, granted in ctx.org_permissions.items() if granted.intersection(perms)]
    return {"scope_all": False, "scope_orgs": orgs}


@dataclass
class Entity:
    kind: str
    id: uuid.UUID
    org: uuid.UUID | None
    status: str
    author_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    row: Any


_LOADERS = {
    "program": "SELECT p.*, p.organization_id AS org FROM programs p WHERE p.id = :id",
    "module": "SELECT m.*, m.organization_id AS org FROM modules m WHERE m.id = :id",
    "lesson": (
        "SELECT l.*, m.organization_id AS org FROM lessons l JOIN modules m ON m.id = l.module_id WHERE l.id = :id"
    ),
    "lesson-versions": (
        "SELECT lv.*, m.organization_id AS org, l.module_id FROM lesson_versions lv "
        "JOIN lessons l ON l.id = lv.lesson_id JOIN modules m ON m.id = l.module_id WHERE lv.id = :id"
    ),
    "module-versions": (
        "SELECT mv.*, m.organization_id AS org, m.code AS module_code FROM module_versions mv "
        "JOIN modules m ON m.id = mv.module_id WHERE mv.id = :id"
    ),
    "releases": (
        "SELECT r.*, p.organization_id AS org, p.is_medical, p.code AS program_code FROM program_releases r "
        "JOIN programs p ON p.id = r.program_id WHERE r.id = :id"
    ),
}
_LOCK_TABLE = {"lesson-versions": "lv", "module-versions": "mv", "releases": "r"}
_PARENT = {
    "lesson": "module_id",
    "lesson-versions": "lesson_id",
    "module-versions": "module_id",
    "releases": "program_id",
}


async def load(
    db: AsyncSession, ctx: AuthContext, kind: str, entity_id: uuid.UUID, *, lock: bool = False, what: str = "Contenu"
) -> Entity:
    sql = _LOADERS[kind]
    if lock:
        sql += f" FOR UPDATE OF {_LOCK_TABLE[kind]}"
    row = (await db.execute(text(sql), {"id": entity_id})).mappings().first()
    if row is None:
        raise not_found(what)
    ensure_visible(ctx, row["org"], what)
    return Entity(
        kind=kind,
        id=row["id"],
        org=row["org"],
        status=row.get("status", "draft"),
        author_id=row.get("author_id") or row.get("created_by"),
        parent_id=row.get(_PARENT.get(kind, ""), None),
        row=row,
    )

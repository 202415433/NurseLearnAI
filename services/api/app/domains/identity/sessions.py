"""Sessions opaques côté serveur (ADR-0007 §C).

- L'identifiant (256 bits) n'est connu que du client ; la base n'en garde qu'une empreinte HMAC.
- Expiration après inactivité (`SESSION_IDLE_TIMEOUT_MINUTES`) et durée absolue
  (`SESSION_ABSOLUTE_LIFETIME_HOURS`, colonne `expires_at`, conservée lors des rotations).
- Opérations sensibles : la « fraîcheur » (`last_authenticated_at`) est perdue dès que l'inactivité
  dépasse `SENSITIVE_IDLE_TIMEOUT_MINUTES` (30 min) ; une ré-authentification la rétablit.
- Rotation : nouvelle ligne de la même famille ; l'ancienne est révoquée (`rotated`). Un identifiant
  remplacé reste toléré `SESSION_REUSE_GRACE_SECONDS` (requêtes parallèles) ; au-delà, sa
  réutilisation révoque toute la famille (vol présumé).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings
from app.core.security import new_token, token_digest


@dataclass
class AuthContext:
    user_id: uuid.UUID
    session_id: uuid.UUID
    family_id: uuid.UUID
    email: str
    locale: str
    status: str
    expires_at: datetime
    created_at: datetime
    sensitive_fresh: bool
    global_permissions: set[str] = field(default_factory=set)
    org_permissions: dict[uuid.UUID, set[str]] = field(default_factory=dict)
    roles: list[tuple[str, uuid.UUID | None]] = field(default_factory=list)

    def can(self, permission: str, organization_id: uuid.UUID | None = None) -> bool:
        if permission in self.global_permissions:
            return True
        return organization_id is not None and permission in self.org_permissions.get(organization_id, set())

    def member_of(self, organization_id: uuid.UUID) -> bool:
        return any(org == organization_id for _, org in self.roles)

    @property
    def primary_role(self) -> str:
        order = ["admin", "org_admin", "medical_reviewer", "instructor", "student"]
        codes = {code for code, _ in self.roles}
        return next((c for c in order if c in codes), "none")


async def create_session(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    *,
    ip: str | None,
    user_agent: str | None,
    family_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    fresh: bool = True,
) -> tuple[str, uuid.UUID, datetime]:
    """Crée une session ; renvoie (identifiant opaque, id de session, expiration absolue)."""
    token = new_token()
    row = (
        await db.execute(
            text(
                "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at, ip, user_agent, family_id, "
                "last_seen_at, last_authenticated_at) VALUES (:u, :h, "
                "COALESCE(:exp, now() + make_interval(hours => :hours)), CAST(CAST(:ip AS text) AS inet), :ua, "
                "COALESCE(:fam, gen_random_uuid()), now(), CASE WHEN :fresh THEN now() END) "
                "RETURNING id, expires_at"
            ),
            {
                "u": user_id,
                "h": token_digest(token, settings.effective_token_hash_key),
                "exp": expires_at,
                "hours": settings.session_absolute_lifetime_hours,
                "ip": ip,
                "ua": (user_agent or "")[:200] or None,
                "fam": family_id,
                "fresh": fresh,
            },
        )
    ).one()
    return token, row.id, row.expires_at


async def revoke_family(db: AsyncSession, family_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = now(), revoked_reason = :r "
            "WHERE family_id = :f AND revoked_at IS NULL"
        ),
        {"f": family_id, "r": reason},
    )


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, reason: str, *, except_family: uuid.UUID | None = None
) -> int:
    result = await db.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = now(), revoked_reason = :r "
            "WHERE user_id = :u AND revoked_at IS NULL AND (CAST(:keep AS uuid) IS NULL OR family_id <> :keep)"
        ),
        {"u": user_id, "r": reason, "keep": except_family},
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def _load_permissions(db: AsyncSession, ctx: AuthContext) -> None:
    rows = (
        await db.execute(
            text(
                "SELECT r.code, ur.organization_id, rp.permission_code FROM user_roles ur "
                "JOIN roles r ON r.id = ur.role_id "
                "LEFT JOIN role_permissions rp ON rp.role_id = ur.role_id WHERE ur.user_id = :u"
            ),
            {"u": ctx.user_id},
        )
    ).all()
    seen: set[tuple[str, uuid.UUID | None]] = set()
    for code, org, perm in rows:
        if (code, org) not in seen:
            seen.add((code, org))
            ctx.roles.append((code, org))
        if perm is None:
            continue
        if org is None:
            ctx.global_permissions.add(perm)
        else:
            ctx.org_permissions.setdefault(org, set()).add(perm)


async def authenticate(
    db: AsyncSession,
    settings: Settings,
    token: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    _depth: int = 0,
) -> AuthContext | None:
    """Valide un identifiant de session ; renvoie None s'il est inconnu, expiré ou révoqué.

    Effets : met à jour l'activité, perd la fraîcheur sensible après 30 min d'inactivité,
    révoque la famille en cas de réutilisation d'un identifiant remplacé.
    """
    if not token or len(token) > 128:
        return None
    row = (
        (
            await db.execute(
                text(
                    "SELECT s.id, s.user_id, s.family_id, s.created_at, s.expires_at, s.revoked_at, "
                    "s.revoked_reason, s.replaced_by, s.last_authenticated_at, "
                    "(s.expires_at <= now()) AS absolute_expired, "
                    "(s.last_seen_at < now() - make_interval(mins => :idle)) AS idle_expired, "
                    "(s.last_seen_at < now() - make_interval(mins => :sens)) AS sensitive_lapsed, "
                    "(s.last_seen_at < now() - interval '30 seconds') AS touch_due, "
                    "(s.rotated_at >= now() - make_interval(secs => :grace)) AS in_grace, "
                    "u.email::text AS email, u.locale, u.status, u.deleted_at "
                    "FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.refresh_token_hash = :h"
                ),
                {
                    "h": token_digest(token, settings.effective_token_hash_key),
                    "idle": settings.session_idle_timeout_minutes,
                    "sens": settings.sensitive_idle_timeout_minutes,
                    "grace": settings.session_reuse_grace_seconds,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    if row["revoked_at"] is not None:
        if row["revoked_reason"] == "rotated" and row["replaced_by"] is not None:
            if row["in_grace"] and _depth == 0:
                # Requête parallèle à la rotation : on sert la session qui la remplace.
                replacement = await db.execute(
                    text("SELECT refresh_token_hash FROM auth_sessions WHERE id = :id"), {"id": row["replaced_by"]}
                )
                if replacement.scalar() is not None:
                    return await _context_for_session(db, settings, row["replaced_by"])
                return None
            # Réutilisation d'un identifiant remplacé hors délai : vol présumé.
            await revoke_family(db, row["family_id"], "reuse_detected")
            await audit(
                db,
                "auth.session_reuse",
                "auth_session",
                row["family_id"],
                actor=row["user_id"],
                ip=ip,
                user_agent=user_agent,
                metadata={"reason": "rotated_identifier_reused"},
            )
            await db.commit()
        return None
    if row["absolute_expired"] or row["idle_expired"]:
        await db.execute(
            text(
                "UPDATE auth_sessions SET revoked_at = now(), revoked_reason = 'expired' "
                "WHERE id = :id AND revoked_at IS NULL"
            ),
            {"id": row["id"]},
        )
        await db.commit()
        return None
    if row["status"] != "active" or row["deleted_at"] is not None:
        return None
    if row["sensitive_lapsed"] and row["last_authenticated_at"] is not None:
        await db.execute(
            text("UPDATE auth_sessions SET last_authenticated_at = NULL WHERE id = :id"), {"id": row["id"]}
        )
    if row["touch_due"] or row["sensitive_lapsed"]:
        await db.execute(text("UPDATE auth_sessions SET last_seen_at = now() WHERE id = :id"), {"id": row["id"]})
        await db.commit()
    ctx = AuthContext(
        user_id=row["user_id"],
        session_id=row["id"],
        family_id=row["family_id"],
        email=row["email"],
        locale=row["locale"],
        status=row["status"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        sensitive_fresh=row["last_authenticated_at"] is not None and not row["sensitive_lapsed"],
    )
    await _load_permissions(db, ctx)
    return ctx


async def _context_for_session(db: AsyncSession, settings: Settings, session_id: uuid.UUID) -> AuthContext | None:
    row = (
        (
            await db.execute(
                text(
                    "SELECT s.id, s.user_id, s.family_id, s.created_at, s.expires_at, s.last_authenticated_at, "
                    "(s.revoked_at IS NULL AND s.expires_at > now()) AS valid, "
                    "(s.last_seen_at < now() - make_interval(mins => :sens)) AS sensitive_lapsed, "
                    "u.email::text AS email, u.locale, u.status, u.deleted_at "
                    "FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.id = :id"
                ),
                {"id": session_id, "sens": settings.sensitive_idle_timeout_minutes},
            )
        )
        .mappings()
        .first()
    )
    if row is None or not row["valid"] or row["status"] != "active" or row["deleted_at"] is not None:
        return None
    ctx = AuthContext(
        user_id=row["user_id"],
        session_id=row["id"],
        family_id=row["family_id"],
        email=row["email"],
        locale=row["locale"],
        status=row["status"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        sensitive_fresh=row["last_authenticated_at"] is not None and not row["sensitive_lapsed"],
    )
    await _load_permissions(db, ctx)
    return ctx


async def rotate(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    *,
    ip: str | None,
    user_agent: str | None,
    force: bool = False,
    mark_fresh: bool = False,
) -> str | None:
    """Remplace l'identifiant si la rotation est due (ou forcée) ; renvoie le nouvel identifiant."""
    if not force:
        due = (
            await db.execute(
                text("SELECT created_at < now() - make_interval(mins => :m) FROM auth_sessions WHERE id = :id"),
                {"id": ctx.session_id, "m": settings.session_rotation_minutes},
            )
        ).scalar()
        if not due:
            return None
    token, new_id, _ = await create_session(
        db,
        settings,
        ctx.user_id,
        ip=ip,
        user_agent=user_agent,
        family_id=ctx.family_id,
        expires_at=ctx.expires_at,  # la rotation ne prolonge jamais la durée absolue
        fresh=mark_fresh or ctx.sensitive_fresh,
    )
    if not (mark_fresh or ctx.sensitive_fresh):
        await db.execute(text("UPDATE auth_sessions SET last_authenticated_at = NULL WHERE id = :id"), {"id": new_id})
    await db.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = now(), revoked_reason = 'rotated', replaced_by = :n, "
            "rotated_at = now() WHERE id = :id AND revoked_at IS NULL"
        ),
        {"id": ctx.session_id, "n": new_id},
    )
    ctx.session_id = new_id
    if mark_fresh:
        ctx.sensitive_fresh = True
    return token


async def rotate_now(
    db: AsyncSession,
    settings: Settings,
    ctx: AuthContext,
    *,
    ip: str | None,
    user_agent: str | None,
    mark_fresh: bool = False,
) -> str:
    """Rotation immédiate (connexion renouvelée, ré-authentification, élévation de privilège)."""
    token = await rotate(db, settings, ctx, ip=ip, user_agent=user_agent, force=True, mark_fresh=mark_fresh)
    if token is None:  # impossible : la rotation forcée crée toujours une session
        raise RuntimeError("rotation de session impossible")
    return token

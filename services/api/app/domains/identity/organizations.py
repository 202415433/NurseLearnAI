"""Établissements, invitations et rôles (ADR-0007 §A et §B).

Isolation : toute ressource d'un établissement n'est visible que par ses membres (rôle dans cet
établissement) ou par un administrateur de la plateforme. Hors périmètre, la réponse est 404
(l'existence de la ressource n'est pas révélée). Les mêmes règles sont garanties en base
(déclencheurs des migrations 0003 et 0004).

Matrice d'invitation :
- administrateur de la plateforme (`user:manage` global) : instructor, medical_reviewer (global),
  org_admin (avec établissement) ;
- administrateur d'établissement (`org:manage` dans l'établissement) : instructor de CET établissement ;
- personne d'autre. Les rôles student et admin ne s'obtiennent jamais par invitation.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings
from app.core.errors import ProblemError
from app.core.mailer import Mailer, render
from app.core.security import new_token, token_digest
from app.domains.identity import schemas as s
from app.domains.identity.deps import (
    PROBLEM_BASE,
    Auth,
    Db,
    SensitiveAuth,
    client_ip,
    forbidden,
    get_settings_dep,
    parse_uuid,
    user_agent,
)
from app.domains.identity.sessions import AuthContext, revoke_user_sessions, rotate_now

router = APIRouter(prefix="/api/v1", tags=["établissements et invitations"])
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]

_ROLE_LABELS = {
    "fr": {
        "instructor": "Formateur",
        "medical_reviewer": "Relecteur médical",
        "org_admin": "Administrateur d'établissement",
    },
    "en": {
        "instructor": "Instructor",
        "medical_reviewer": "Medical reviewer",
        "org_admin": "Organization administrator",
    },
}


def _not_found() -> ProblemError:
    return ProblemError(404, "Ressource introuvable", type_=PROBLEM_BASE + "not-found")


def can_assign(ctx: AuthContext, role: str, organization_id: uuid.UUID | None) -> bool:
    """Règle applicative identique à nl_role_grant_allowed (base) : moindre privilège."""
    if role in ("student", "admin"):
        return False
    if ctx.can("user:manage"):  # administrateur de la plateforme (permission globale)
        return True
    return role == "instructor" and organization_id is not None and ctx.can("org:manage", organization_id)


def _visible_org(ctx: AuthContext, organization_id: uuid.UUID) -> bool:
    return ctx.can("user:manage") or ctx.member_of(organization_id)


async def _org_exists(db: AsyncSession, organization_id: uuid.UUID) -> bool:
    return (
        await db.execute(
            text("SELECT 1 FROM organizations WHERE id = :o AND deleted_at IS NULL"), {"o": organization_id}
        )
    ).first() is not None


# --------------------------------------------------------------------------------------------
# Établissements (données privées isolées)
# --------------------------------------------------------------------------------------------
@router.get("/organizations/mine", response_model=list[s.OrganizationOut], summary="Mes établissements")
async def my_organizations(ctx: Auth, db: Db) -> list[s.OrganizationOut]:
    rows = (
        await db.execute(
            text(
                "SELECT o.id, o.slug, o.name, array_agg(DISTINCT r.code ORDER BY r.code) AS roles "
                "FROM user_roles ur JOIN organizations o ON o.id = ur.organization_id "
                "JOIN roles r ON r.id = ur.role_id "
                "WHERE ur.user_id = :u AND o.deleted_at IS NULL GROUP BY o.id, o.slug, o.name ORDER BY o.name"
            ),
            {"u": ctx.user_id},
        )
    ).all()
    return [s.OrganizationOut(id=r.id, slug=r.slug, name=r.name, roles=list(r.roles)) for r in rows]


@router.get("/admin/organizations", response_model=list[s.OrganizationOut], summary="Établissements (administration)")
async def all_organizations(ctx: Auth, db: Db) -> list[s.OrganizationOut]:
    if not ctx.can("user:manage"):
        raise _not_found()
    rows = (
        await db.execute(
            text("SELECT id, slug, name FROM organizations WHERE deleted_at IS NULL ORDER BY name LIMIT 500")
        )
    ).all()
    return [s.OrganizationOut(id=r.id, slug=r.slug, name=r.name, roles=[]) for r in rows]


@router.get("/organizations/{org_id}/members", response_model=list[s.MemberOut], summary="Membres (gestion)")
async def members(org_id: str, ctx: Auth, db: Db) -> list[s.MemberOut]:
    oid = parse_uuid(org_id)
    if not ctx.can("org:manage", oid) or not await _org_exists(db, oid):
        raise _not_found()
    rows = (
        await db.execute(
            text(
                "SELECT u.id, COALESCE(p.display_name, '') AS name, u.email::text AS email, "
                "array_agg(DISTINCT r.code ORDER BY r.code) AS roles FROM user_roles ur "
                "JOIN users u ON u.id = ur.user_id JOIN roles r ON r.id = ur.role_id "
                "LEFT JOIN profiles p ON p.user_id = u.id "
                "WHERE ur.organization_id = :o AND u.deleted_at IS NULL GROUP BY u.id, p.display_name, u.email "
                "ORDER BY name"
            ),
            {"o": oid},
        )
    ).all()
    return [s.MemberOut(user_id=r.id, display_name=r.name, email=r.email, roles=list(r.roles)) for r in rows]


@router.get("/organizations/{org_id}/programs", response_model=list[s.PrivateProgramOut], summary="Programmes privés")
async def private_programs(org_id: str, ctx: Auth, db: Db, request: Request) -> list[s.PrivateProgramOut]:
    oid = parse_uuid(org_id)
    if not _visible_org(ctx, oid) or not await _org_exists(db, oid):
        raise _not_found()
    locale = getattr(request.state, "locale", None) or ctx.locale
    rows = (
        await db.execute(
            text(
                "SELECT id, code, COALESCE(title_i18n ->> :l, title_i18n ->> 'fr') AS title, status "
                "FROM programs WHERE organization_id = :o ORDER BY code"
            ),
            {"o": oid, "l": locale},
        )
    ).all()
    return [s.PrivateProgramOut(id=r.id, code=r.code, title=r.title, status=r.status) for r in rows]


# --------------------------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------------------------
def _status(row) -> Literal["pending", "accepted", "revoked", "expired"]:  # type: ignore[no-untyped-def]
    if row.accepted_at is not None:
        return "accepted"
    if row.revoked_at is not None:
        return "revoked"
    return "expired" if row.expired else "pending"


@router.get(
    "/organizations/{org_id}/invitations",
    response_model=list[s.InvitationOut],
    summary="Invitations d'un établissement",
)
async def org_invitations(org_id: str, ctx: Auth, db: Db) -> list[s.InvitationOut]:
    oid = parse_uuid(org_id)
    if not ctx.can("org:manage", oid) or not await _org_exists(db, oid):
        raise _not_found()
    return await _list_invitations(db, oid)


@router.get("/invitations", response_model=list[s.InvitationOut], summary="Invitations globales (administration)")
async def global_invitations(ctx: Auth, db: Db) -> list[s.InvitationOut]:
    if not ctx.can("user:manage"):
        raise _not_found()
    return await _list_invitations(db, None)


async def _list_invitations(db: AsyncSession, oid: uuid.UUID | None) -> list[s.InvitationOut]:
    rows = (
        await db.execute(
            text(
                "SELECT i.id, i.email::text AS email, r.code AS role, i.organization_id, i.created_at, i.expires_at, "
                "i.accepted_at, "
                "i.revoked_at, (i.expires_at <= now()) AS expired FROM invitations i JOIN roles r ON r.id = i.role_id "
                "WHERE i.organization_id IS NOT DISTINCT FROM :o ORDER BY i.created_at DESC LIMIT 200"
            ),
            {"o": oid},
        )
    ).all()
    return [
        s.InvitationOut(
            id=r.id,
            email=r.email,
            role=r.role,
            organization_id=r.organization_id,
            status=_status(r),
            created_at=r.created_at,
            expires_at=r.expires_at,
        )
        for r in rows
    ]


@router.post("/invitations", status_code=201, response_model=s.InvitationCreated, summary="Inviter (sensible)")
async def create_invitation(
    body: s.InvitationIn, request: Request, ctx: SensitiveAuth, db: Db, settings: SettingsDep
) -> s.InvitationCreated:
    org = body.organization_id
    if org is not None and (not _visible_org(ctx, org) or not await _org_exists(db, org)):
        raise _not_found()
    if body.role == "org_admin" and org is None:
        raise ProblemError(422, "Un établissement est requis pour ce rôle", type_=PROBLEM_BASE + "invalid-invitation")
    if body.role == "medical_reviewer" and org is not None:
        raise ProblemError(422, "Le rôle de relecteur médical est global", type_=PROBLEM_BASE + "invalid-invitation")
    if not can_assign(ctx, body.role, org):
        raise forbidden("Vous ne pouvez pas attribuer ce rôle")
    # Les invitations échues ne bloquent pas une nouvelle invitation.
    await db.execute(
        text(
            "UPDATE invitations SET revoked_at = now() WHERE email = CAST(:e AS text) "
            "AND organization_id IS NOT DISTINCT FROM :o "
            "AND role_id = (SELECT id FROM roles WHERE code = :r) AND accepted_at IS NULL AND revoked_at IS NULL "
            "AND expires_at <= now()"
        ),
        {"e": body.email, "o": org, "r": body.role},
    )
    token = new_token()
    try:
        row = (
            await db.execute(
                text(
                    "INSERT INTO invitations (email, role_id, organization_id, invited_by, token_hash, expires_at) "
                    "SELECT CAST(:e AS text), id, :o, :by, :h, now() + make_interval(hours => :ttl) "
                    "FROM roles WHERE code = :r "
                    "RETURNING id, expires_at"
                ),
                {
                    "e": body.email,
                    "o": org,
                    "by": ctx.user_id,
                    "h": token_digest(token, settings.effective_token_hash_key),
                    "ttl": settings.invitation_ttl_hours,
                    "r": body.role,
                },
            )
        ).one()
    except IntegrityError as exc:
        await db.rollback()
        raise ProblemError(
            409, "Une invitation est déjà en attente pour cette personne", type_=PROBLEM_BASE + "conflict"
        ) from exc
    org_name = (
        (await db.execute(text("SELECT name FROM organizations WHERE id = :o"), {"o": org})).scalar() if org else None
    )
    await audit(
        db,
        "invitation.created",
        "invitation",
        row.id,
        actor=ctx.user_id,
        actor_role=ctx.primary_role,
        organization_id=org,
        ip=client_ip(request),
        metadata={"role": body.role},
    )
    await db.commit()
    locale = body.locale
    org_text = (f" — {org_name}" if org_name else "") if locale == "en" else (f" ({org_name})" if org_name else "")
    await _mailer(request).send(
        render(
            "invitation",
            locale,
            body.email,
            role=_ROLE_LABELS[locale][body.role],
            org=org_text,
            link=f"{settings.public_web_url.rstrip('/')}/{locale}/invitation?jeton={token}",
            ttl=f"{settings.invitation_ttl_hours} {'heures' if locale == 'fr' else 'hours'}",
        )
    )
    return s.InvitationCreated(id=row.id, expires_at=row.expires_at)


def _mailer(request: Request) -> Mailer:
    return request.app.state.mailer


@router.delete("/invitations/{invitation_id}", status_code=204, summary="Révoquer une invitation")
async def revoke_invitation(invitation_id: str, request: Request, ctx: Auth, db: Db) -> Response:
    iid = parse_uuid(invitation_id)
    inv = (
        await db.execute(
            text(
                "SELECT i.id, r.code AS role, i.organization_id FROM invitations i JOIN roles r ON r.id = i.role_id "
                "WHERE i.id = :id AND i.accepted_at IS NULL AND i.revoked_at IS NULL"
            ),
            {"id": iid},
        )
    ).first()
    if inv is None or not can_assign(ctx, inv.role, inv.organization_id):
        raise _not_found()
    await db.execute(
        text("UPDATE invitations SET revoked_at = now(), revoked_by = :by WHERE id = :id"),
        {"id": iid, "by": ctx.user_id},
    )
    await audit(
        db,
        "invitation.revoked",
        "invitation",
        iid,
        actor=ctx.user_id,
        organization_id=inv.organization_id,
        ip=client_ip(request),
        metadata={"role": inv.role},
    )
    await db.commit()
    return Response(status_code=204)


async def _pending_invitation(db: AsyncSession, settings: Settings, token: str):  # type: ignore[no-untyped-def]
    return (
        await db.execute(
            text(
                "SELECT i.id, i.email::text AS email, i.role_id, r.code AS role, i.organization_id, i.invited_by, "
                "i.expires_at, "
                "o.name AS organization_name FROM invitations i JOIN roles r ON r.id = i.role_id "
                "LEFT JOIN organizations o ON o.id = i.organization_id "
                "WHERE i.token_hash = :h AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()"
            ),
            {"h": token_digest(token, settings.effective_token_hash_key)},
        )
    ).first()


@router.post("/invitations/inspect", response_model=s.InvitationPreview, summary="Aperçu d'une invitation")
async def inspect_invitation(body: s.TokenIn, ctx: Auth, db: Db, settings: SettingsDep) -> s.InvitationPreview:
    inv = await _pending_invitation(db, settings, body.token)
    if inv is None:
        raise ProblemError(400, "Invitation invalide ou expirée", type_=PROBLEM_BASE + "invalid-token")
    return s.InvitationPreview(role=inv.role, organization_name=inv.organization_name, expires_at=inv.expires_at)


@router.post("/invitations/accept", response_model=s.SessionOut, summary="Accepter une invitation")
async def accept_invitation(
    body: s.TokenIn, request: Request, ctx: Auth, db: Db, settings: SettingsDep
) -> s.SessionOut:
    invalid = ProblemError(400, "Invitation invalide ou expirée", type_=PROBLEM_BASE + "invalid-token")
    inv = await _pending_invitation(db, settings, body.token)
    if inv is None:
        raise invalid
    verified = (
        await db.execute(text("SELECT email_verified_at IS NOT NULL FROM users WHERE id = :u"), {"u": ctx.user_id})
    ).scalar()
    if not verified or inv.email.lower() != ctx.email.lower():
        await audit(
            db,
            "invitation.rejected",
            "invitation",
            inv.id,
            actor=ctx.user_id,
            organization_id=inv.organization_id,
            ip=client_ip(request),
            metadata={"reason": "email_mismatch"},
        )
        await db.commit()
        raise forbidden("Cette invitation ne correspond pas à votre adresse vérifiée")
    # L'émetteur doit toujours détenir le droit d'attribuer ce rôle (même règle qu'en base).
    allowed = (
        await db.execute(
            text("SELECT nl_role_grant_allowed(:by, :u, :r, :o)"),
            {"by": inv.invited_by, "u": ctx.user_id, "r": inv.role, "o": inv.organization_id},
        )
    ).scalar()
    if not allowed:
        await db.execute(text("UPDATE invitations SET revoked_at = now() WHERE id = :id"), {"id": inv.id})
        await audit(
            db,
            "invitation.rejected",
            "invitation",
            inv.id,
            actor=ctx.user_id,
            organization_id=inv.organization_id,
            metadata={"reason": "inviter_no_longer_authorized"},
        )
        await db.commit()
        raise invalid
    claimed = (
        await db.execute(
            text(
                "UPDATE invitations SET accepted_at = now(), accepted_by = :u "
                "WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING id"
            ),
            {"id": inv.id, "u": ctx.user_id},
        )
    ).first()
    if claimed is None:  # acceptée en parallèle
        await db.rollback()
        raise invalid
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id, organization_id, granted_by) VALUES (:u, :r, :o, :by) "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": ctx.user_id, "r": inv.role_id, "o": inv.organization_id, "by": inv.invited_by},
    )
    for action in ("invitation.accepted", "role.granted"):
        await audit(
            db,
            action,
            "invitation" if action.startswith("invitation") else "user",
            inv.id if action.startswith("invitation") else ctx.user_id,
            actor=ctx.user_id,
            organization_id=inv.organization_id,
            ip=client_ip(request),
            metadata={"role": inv.role, "granted_by": str(inv.invited_by)},
        )
    # Élévation de privilège : nouvel identifiant de session (ADR-0007, rotation).
    token = await rotate_now(db, settings, ctx, ip=client_ip(request), user_agent=user_agent(request))
    await db.commit()
    return s.SessionOut(session_token=token, expires_at=ctx.expires_at)


# --------------------------------------------------------------------------------------------
# Retrait d'un rôle (révocation des accès)
# --------------------------------------------------------------------------------------------
@router.delete(
    "/organizations/{org_id}/members/{user_id}/roles/{role}", status_code=204, summary="Retirer un rôle (sensible)"
)
async def revoke_org_role(
    org_id: str, user_id: str, role: str, request: Request, ctx: SensitiveAuth, db: Db
) -> Response:
    oid, uid = parse_uuid(org_id), parse_uuid(user_id)
    return await _revoke_role(db, request, ctx, uid, role, oid)


@router.delete("/admin/users/{user_id}/roles/{role}", status_code=204, summary="Retirer un rôle global (sensible)")
async def revoke_global_role(user_id: str, role: str, request: Request, ctx: SensitiveAuth, db: Db) -> Response:
    if not ctx.can("user:manage"):
        raise _not_found()
    return await _revoke_role(db, request, ctx, parse_uuid(user_id), role, None)


async def _revoke_role(
    db: AsyncSession, request: Request, ctx: AuthContext, uid: uuid.UUID, role: str, oid: uuid.UUID | None
) -> Response:
    if oid is not None and not ctx.can("org:manage", oid):
        raise _not_found()
    if role == "student" or not can_assign(ctx, role, oid) or uid == ctx.user_id:
        raise forbidden("Vous ne pouvez pas retirer ce rôle")
    removed = (
        await db.execute(
            text(
                "DELETE FROM user_roles WHERE user_id = :u AND organization_id IS NOT DISTINCT FROM :o "
                "AND role_id = (SELECT id FROM roles WHERE code = :r) RETURNING id"
            ),
            {"u": uid, "o": oid, "r": role},
        )
    ).first()
    if removed is None:
        raise _not_found()
    # Les sessions de la personne sont fermées : ses nouveaux droits s'appliquent immédiatement.
    await revoke_user_sessions(db, uid, "role_revoked")
    await audit(
        db,
        "role.revoked",
        "user",
        uid,
        actor=ctx.user_id,
        actor_role=ctx.primary_role,
        organization_id=oid,
        ip=client_ip(request),
        metadata={"role": role},
    )
    await db.commit()
    return Response(status_code=204)

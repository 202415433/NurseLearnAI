"""Authentification et compte : inscription, vérification, connexion, sessions, mot de passe, profil.

Principes (ADR-0007) :
- l'inscription publique n'attribue que le rôle « student » (aucun champ de rôle accepté) ;
- réponses identiques que le compte existe ou non (inscription, renvoi, mot de passe oublié) ;
  un échec de connexion renvoie toujours le même message ;
- jetons de 256 bits stockés en empreinte HMAC, à usage unique, à durée limitée ;
- chaque événement de sécurité est écrit dans audit_logs, sans secret ni courriel en clair.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings
from app.core.errors import ProblemError
from app.core.limits import within_limit
from app.core.mailer import Mailer, render
from app.core.security import (
    hash_password,
    needs_rehash,
    new_token,
    password_problems,
    pseudonymize,
    token_digest,
    verify_password,
)
from app.domains.identity import schemas as s
from app.domains.identity.deps import (
    PROBLEM_BASE,
    Auth,
    Db,
    SensitiveAuth,
    client_ip,
    get_settings_dep,
    parse_uuid,
    user_agent,
)
from app.domains.identity.sessions import (
    AuthContext,
    authenticate,
    create_session,
    revoke_family,
    revoke_user_sessions,
    rotate,
    rotate_now,
)

router = APIRouter(prefix="/api/v1", tags=["identité"])
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]

ACCEPTED = s.Accepted(status="accepted")
_INVALID_CREDENTIALS = ProblemError(
    401,
    "Courriel ou mot de passe incorrect, ou compte temporairement verrouillé",
    type_=PROBLEM_BASE + "invalid-credentials",
)


def _too_many() -> ProblemError:
    return ProblemError(429, "Trop de tentatives, réessayez plus tard", type_=PROBLEM_BASE + "rate-limit")


def _invalid_token() -> ProblemError:
    return ProblemError(400, "Lien invalide ou expiré", type_=PROBLEM_BASE + "invalid-token")


def _weak_password(problems: list[str]) -> ProblemError:
    return ProblemError(
        422, "Mot de passe refusé", type_=PROBLEM_BASE + "weak-password", extra={"password_errors": problems}
    )


def _mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def _ttl(hours: float, locale: str) -> str:
    if hours < 1:
        minutes = round(hours * 60)
        return f"{minutes} minutes"
    whole = round(hours)
    return f"{whole} heures" if locale == "fr" else f"{whole} hours"


def _link(settings: Settings, locale: str, path: str, token: str | None = None) -> str:
    base = f"{settings.public_web_url.rstrip('/')}/{locale}/{path}"
    return f"{base}?jeton={token}" if token else base


async def _issue_token(db: AsyncSession, settings: Settings, user_id: uuid.UUID, purpose: str, minutes: int) -> str:
    """Invalide les jetons encore valides du même usage puis en émet un nouveau."""
    await db.execute(
        text("UPDATE one_time_tokens SET used_at = now() WHERE user_id = :u AND purpose = :p AND used_at IS NULL"),
        {"u": user_id, "p": purpose},
    )
    token = new_token()
    await db.execute(
        text(
            "INSERT INTO one_time_tokens (user_id, purpose, token_hash, expires_at) "
            "VALUES (:u, :p, :h, now() + make_interval(mins => :m))"
        ),
        {"u": user_id, "p": purpose, "h": token_digest(token, settings.effective_token_hash_key), "m": minutes},
    )
    return token


async def _consume_token(db: AsyncSession, settings: Settings, token: str, purpose: str) -> uuid.UUID | None:
    """Consomme un jeton valide (usage unique, verrouillé) ; renvoie l'utilisateur ou None."""
    row = (
        await db.execute(
            text(
                "UPDATE one_time_tokens SET used_at = now() "
                "WHERE token_hash = :h AND purpose = :p AND used_at IS NULL AND expires_at > now() "
                "RETURNING user_id"
            ),
            {"h": token_digest(token, settings.effective_token_hash_key), "p": purpose},
        )
    ).first()
    return row.user_id if row else None


async def build_me(db: AsyncSession, ctx: AuthContext) -> s.MeOut:
    profile = (
        await db.execute(text("SELECT display_name FROM profiles WHERE user_id = :u"), {"u": ctx.user_id})
    ).scalar()
    rows = (
        await db.execute(
            text(
                "SELECT o.id, o.name FROM organizations o WHERE o.id IN "
                "(SELECT organization_id FROM user_roles WHERE user_id = :u AND organization_id IS NOT NULL)"
            ),
            {"u": ctx.user_id},
        )
    ).all()
    orgs: dict[uuid.UUID, str] = {r.id: r.name for r in rows}
    return s.MeOut(
        id=ctx.user_id,
        email=ctx.email,
        display_name=profile or "",
        locale=ctx.locale,
        status=ctx.status,
        roles=[
            s.RoleOut(role=code, organization_id=org, organization_name=orgs.get(org) if org else None)
            for code, org in sorted(ctx.roles, key=lambda r: (r[0], str(r[1])))
        ],
        permissions=sorted(ctx.global_permissions),
        sensitive_fresh=ctx.sensitive_fresh,
        session_expires_at=ctx.expires_at,
    )


# --------------------------------------------------------------------------------------------
# Inscription et vérification du courriel
# --------------------------------------------------------------------------------------------
@router.post("/auth/register", status_code=202, response_model=s.Accepted, summary="Inscription étudiante")
async def register(body: s.RegisterIn, request: Request, db: Db, settings: SettingsDep) -> s.Accepted:
    ip = client_ip(request) or "inconnue"
    if not await within_limit(request, "register_ip", ip, settings.register_per_hour_per_ip, 3600):
        raise _too_many()
    if not await within_limit(request, "register_addr", body.email, settings.email_actions_per_hour_per_address, 3600):
        return ACCEPTED  # même réponse : ne révèle rien sur l'adresse
    if not (body.adult_attestation and body.accept_terms and body.accept_privacy):
        missing = [
            name
            for name, ok in (
                ("adult_attestation", body.adult_attestation),
                ("accept_terms", body.accept_terms),
                ("accept_privacy", body.accept_privacy),
            )
            if not ok
        ]
        raise ProblemError(
            422,
            "Déclarations obligatoires manquantes",
            type_=PROBLEM_BASE + "consent-required",
            extra={"missing": missing},
        )
    problems = password_problems(body.password, body.email)
    if problems:
        raise _weak_password(problems)
    password_hash = hash_password(body.password)  # calculé dans tous les cas : temps de réponse comparable
    if body.website:
        await audit(db, "user.register_blocked", "user", None, ip=client_ip(request), metadata={"reason": "honeypot"})
        await db.commit()
        return ACCEPTED

    existing = (
        await db.execute(
            text("SELECT id, locale FROM users WHERE email = CAST(:e AS text) AND deleted_at IS NULL"),
            {"e": body.email},
        )
    ).first()
    if existing is not None:
        await _mailer(request).send(
            render("account_exists", existing.locale, body.email, link=_link(settings, existing.locale, "connexion"))
        )
        return ACCEPTED
    try:
        user_id = (
            await db.execute(
                text(
                    "INSERT INTO users (email, password_hash, status, locale, adult_attested_at, terms_version, "
                    "terms_accepted_at, privacy_policy_version, privacy_accepted_at, password_changed_at) "
                    "VALUES (CAST(:e AS text), :h, 'pending_verification', :l, now(), :tv, now(), :pv, now(), now()) "
                    "RETURNING id"
                ),
                {
                    "e": body.email,
                    "h": password_hash,
                    "l": body.locale,
                    "tv": settings.terms_version,
                    "pv": settings.privacy_policy_version,
                },
            )
        ).scalar_one()
    except IntegrityError:
        await db.rollback()
        return ACCEPTED  # inscription concurrente avec la même adresse
    await db.execute(
        text("INSERT INTO profiles (user_id, display_name) VALUES (:u, :n)"), {"u": user_id, "n": body.display_name}
    )
    # Seul rôle attribuable sans émetteur (garde en base : migration 0004).
    await db.execute(
        text("INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE code = 'student'"),
        {"u": user_id},
    )
    token = await _issue_token(db, settings, user_id, "email_verification", settings.email_verification_ttl_hours * 60)
    await audit(
        db,
        "user.register",
        "user",
        user_id,
        actor=user_id,
        actor_role="student",
        ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"terms_version": settings.terms_version, "privacy_policy_version": settings.privacy_policy_version},
    )
    await db.commit()
    await _mailer(request).send(
        render(
            "verify_email",
            body.locale,
            body.email,
            link=_link(settings, body.locale, "verification", token),
            ttl=_ttl(settings.email_verification_ttl_hours, body.locale),
        )
    )
    return ACCEPTED


@router.post("/auth/verify-email", response_model=s.Accepted, summary="Vérification du courriel")
async def verify_email(body: s.TokenIn, request: Request, db: Db, settings: SettingsDep) -> s.Accepted:
    user_id = await _consume_token(db, settings, body.token, "email_verification")
    if user_id is None:
        await db.rollback()
        raise _invalid_token()
    await db.execute(
        text(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, now()), "
            "status = CASE WHEN status = 'pending_verification' THEN 'active' ELSE status END WHERE id = :u"
        ),
        {"u": user_id},
    )
    await audit(db, "user.email_verified", "user", user_id, actor=user_id, ip=client_ip(request))
    await db.commit()
    return s.Accepted(status="verified")


@router.post("/auth/resend-verification", status_code=202, response_model=s.Accepted)
async def resend_verification(body: s.EmailIn, request: Request, db: Db, settings: SettingsDep) -> s.Accepted:
    ip = client_ip(request) or "inconnue"
    if not await within_limit(request, "email_ip", ip, settings.email_actions_per_hour_per_ip, 3600):
        raise _too_many()
    if not await within_limit(request, "resend_addr", body.email, settings.email_actions_per_hour_per_address, 3600):
        return ACCEPTED
    user = (
        await db.execute(
            text(
                "SELECT id, locale FROM users WHERE email = CAST(:e AS text) AND deleted_at IS NULL "
                "AND status = 'pending_verification'"
            ),
            {"e": body.email},
        )
    ).first()
    if user is not None:
        token = await _issue_token(
            db, settings, user.id, "email_verification", settings.email_verification_ttl_hours * 60
        )
        await db.commit()
        await _mailer(request).send(
            render(
                "verify_email",
                user.locale,
                body.email,
                link=_link(settings, user.locale, "verification", token),
                ttl=_ttl(settings.email_verification_ttl_hours, user.locale),
            )
        )
    return ACCEPTED


# --------------------------------------------------------------------------------------------
# Connexion, sessions, déconnexion
# --------------------------------------------------------------------------------------------
@router.post("/auth/login", response_model=s.LoginOut, summary="Connexion")
async def login(body: s.LoginIn, request: Request, db: Db, settings: SettingsDep) -> s.LoginOut:
    ip = client_ip(request)
    if not await within_limit(request, "login_ip", ip or "inconnue", settings.login_per_10min_per_ip, 600):
        raise _too_many()
    user = (
        await db.execute(
            text(
                "SELECT id, password_hash, status, locale, (locked_until > now()) AS locked, failed_login_count "
                "FROM users WHERE email = CAST(:e AS text) AND deleted_at IS NULL"
            ),
            {"e": body.email},
        )
    ).first()
    ok = verify_password(user.password_hash if user else None, body.password)
    if user is None or user.locked or not ok:
        if user is not None and not user.locked:
            failures = user.failed_login_count + 1
            lock = failures % settings.login_max_failures == 0
            await db.execute(
                text(
                    "UPDATE users SET failed_login_count = :n, "
                    "locked_until = CASE WHEN :lock THEN now() + make_interval(mins => :m) ELSE locked_until END "
                    "WHERE id = :u"
                ),
                {"n": failures, "lock": lock, "m": settings.login_lockout_minutes, "u": user.id},
            )
            if lock:
                await _mailer(request).send(
                    render(
                        "new_login_locked",
                        user.locale,
                        body.email,
                        link=_link(settings, user.locale, "mot-de-passe-oublie"),
                    )
                )
        await audit(
            db,
            "auth.login_failed",
            "user",
            user.id if user else None,
            ip=ip,
            user_agent=user_agent(request),
            metadata={
                "reason": "unknown_account" if user is None else ("locked" if user.locked else "bad_password"),
                "email_pseudonym": pseudonymize(body.email, settings.effective_token_hash_key),
            },
        )
        await db.commit()
        raise _INVALID_CREDENTIALS
    if user.status == "pending_verification":
        # Le mot de passe est correct : indiquer l'étape manquante ne révèle rien à un tiers.
        raise ProblemError(403, "Adresse courriel non vérifiée", type_=PROBLEM_BASE + "email-not-verified")
    if user.status != "active":
        await audit(db, "auth.login_failed", "user", user.id, ip=ip, metadata={"reason": f"status_{user.status}"})
        await db.commit()
        raise _INVALID_CREDENTIALS
    new_hash = hash_password(body.password) if needs_rehash(user.password_hash) else None
    await db.execute(
        text(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL, last_login_at = now(), "
            "password_hash = COALESCE(:h, password_hash) WHERE id = :u"
        ),
        {"u": user.id, "h": new_hash},
    )
    token, _, expires_at = await create_session(db, settings, user.id, ip=ip, user_agent=user_agent(request))
    await audit(db, "auth.login", "user", user.id, actor=user.id, ip=ip, user_agent=user_agent(request))
    await db.commit()
    ctx = await authenticate(db, settings, token)
    if ctx is None:  # pragma: no cover — session créée à l'instant
        raise _INVALID_CREDENTIALS
    return s.LoginOut(session_token=token, expires_at=expires_at, user=await build_me(db, ctx))


@router.post("/auth/session/refresh", response_model=s.RefreshOut, summary="Activité et rotation de session")
async def refresh(request: Request, ctx: Auth, db: Db, settings: SettingsDep) -> s.RefreshOut:
    token = await rotate(db, settings, ctx, ip=client_ip(request), user_agent=user_agent(request))
    await db.commit()
    return s.RefreshOut(
        rotated=token is not None, session_token=token, expires_at=ctx.expires_at, sensitive_fresh=ctx.sensitive_fresh
    )


@router.post("/auth/reauthenticate", response_model=s.SessionOut, summary="Ré-authentification (opérations sensibles)")
async def reauthenticate(
    body: s.PasswordIn, request: Request, ctx: Auth, db: Db, settings: SettingsDep
) -> s.SessionOut:
    stored = (await db.execute(text("SELECT password_hash FROM users WHERE id = :u"), {"u": ctx.user_id})).scalar()
    if not verify_password(stored, body.password):
        await audit(db, "auth.reauth_failed", "user", ctx.user_id, actor=ctx.user_id, ip=client_ip(request))
        await db.commit()
        raise _INVALID_CREDENTIALS
    token = await rotate_now(db, settings, ctx, ip=client_ip(request), user_agent=user_agent(request), mark_fresh=True)
    await audit(db, "auth.reauthenticated", "user", ctx.user_id, actor=ctx.user_id, ip=client_ip(request))
    await db.commit()
    return s.SessionOut(session_token=token, expires_at=ctx.expires_at)


@router.post("/auth/logout", status_code=204, summary="Déconnexion (session courante)")
async def logout(request: Request, ctx: Auth, db: Db) -> Response:
    await revoke_family(db, ctx.family_id, "logout")
    await audit(db, "auth.logout", "user", ctx.user_id, actor=ctx.user_id, ip=client_ip(request))
    await db.commit()
    return Response(status_code=204)


@router.post("/auth/logout-all", status_code=204, summary="Déconnexion de tous les appareils (sensible)")
async def logout_all(request: Request, ctx: SensitiveAuth, db: Db) -> Response:
    count = await revoke_user_sessions(db, ctx.user_id, "logout_all")
    await audit(
        db, "auth.logout_all", "user", ctx.user_id, actor=ctx.user_id, ip=client_ip(request), metadata={"count": count}
    )
    await db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------------------------
# Mot de passe
# --------------------------------------------------------------------------------------------
@router.post("/auth/password/forgot", status_code=202, response_model=s.Accepted, summary="Mot de passe oublié")
async def forgot_password(body: s.EmailIn, request: Request, db: Db, settings: SettingsDep) -> s.Accepted:
    ip = client_ip(request) or "inconnue"
    if not await within_limit(request, "email_ip", ip, settings.email_actions_per_hour_per_ip, 3600):
        raise _too_many()
    if not await within_limit(request, "forgot_addr", body.email, settings.email_actions_per_hour_per_address, 3600):
        return ACCEPTED
    user = (
        await db.execute(
            text(
                "SELECT id, locale FROM users WHERE email = CAST(:e AS text) AND deleted_at IS NULL "
                "AND status IN ('active', 'pending_verification')"
            ),
            {"e": body.email},
        )
    ).first()
    if user is not None:
        token = await _issue_token(db, settings, user.id, "password_reset", settings.password_reset_ttl_minutes)
        await audit(db, "password.reset_requested", "user", user.id, ip=client_ip(request))
        await db.commit()
        await _mailer(request).send(
            render(
                "password_reset",
                user.locale,
                body.email,
                link=_link(settings, user.locale, "reinitialisation", token),
                ttl=_ttl(settings.password_reset_ttl_minutes / 60, user.locale),
            )
        )
    return ACCEPTED


@router.post("/auth/password/reset", response_model=s.Accepted, summary="Réinitialisation du mot de passe")
async def reset_password(body: s.ResetIn, request: Request, db: Db, settings: SettingsDep) -> s.Accepted:
    owner = (
        await db.execute(
            text(
                "SELECT u.id, u.email::text AS email, u.locale FROM one_time_tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = :h AND t.purpose = 'password_reset' AND t.used_at IS NULL "
                "AND t.expires_at > now() AND u.deleted_at IS NULL"
            ),
            {"h": token_digest(body.token, settings.effective_token_hash_key)},
        )
    ).first()
    if owner is None:
        raise _invalid_token()
    problems = password_problems(body.new_password, owner.email)
    if problems:
        raise _weak_password(problems)  # le jeton reste valable : la personne peut réessayer
    if await _consume_token(db, settings, body.token, "password_reset") != owner.id:
        await db.rollback()
        raise _invalid_token()
    await db.execute(
        text(
            "UPDATE users SET password_hash = :h, password_changed_at = now(), failed_login_count = 0, "
            "locked_until = NULL, email_verified_at = COALESCE(email_verified_at, now()), "
            "status = CASE WHEN status = 'pending_verification' THEN 'active' ELSE status END WHERE id = :u"
        ),
        {"h": hash_password(body.new_password), "u": owner.id},
    )
    revoked = await revoke_user_sessions(db, owner.id, "password_reset")
    await audit(
        db, "password.reset", "user", owner.id, actor=owner.id, ip=client_ip(request), metadata={"sessions": revoked}
    )
    await db.commit()
    await _mailer(request).send(
        render("password_changed", owner.locale, owner.email, link=_link(settings, owner.locale, "mot-de-passe-oublie"))
    )
    return s.Accepted(status="password_reset")


@router.post("/auth/password/change", response_model=s.SessionOut, summary="Changement de mot de passe (sensible)")
async def change_password(
    body: s.ChangePasswordIn, request: Request, ctx: SensitiveAuth, db: Db, settings: SettingsDep
) -> s.SessionOut:
    stored = (await db.execute(text("SELECT password_hash FROM users WHERE id = :u"), {"u": ctx.user_id})).scalar()
    if not verify_password(stored, body.current_password):
        raise _INVALID_CREDENTIALS
    problems = password_problems(body.new_password, ctx.email)
    if problems:
        raise _weak_password(problems)
    await db.execute(
        text("UPDATE users SET password_hash = :h, password_changed_at = now() WHERE id = :u"),
        {"h": hash_password(body.new_password), "u": ctx.user_id},
    )
    await revoke_user_sessions(db, ctx.user_id, "password_changed", except_family=ctx.family_id)
    token = await rotate_now(db, settings, ctx, ip=client_ip(request), user_agent=user_agent(request), mark_fresh=True)
    await audit(db, "password.changed", "user", ctx.user_id, actor=ctx.user_id, ip=client_ip(request))
    await db.commit()
    await _mailer(request).send(
        render("password_changed", ctx.locale, ctx.email, link=_link(settings, ctx.locale, "mot-de-passe-oublie"))
    )
    return s.SessionOut(session_token=token, expires_at=ctx.expires_at)


# --------------------------------------------------------------------------------------------
# Compte courant
# --------------------------------------------------------------------------------------------
@router.get("/me", response_model=s.MeOut, summary="Compte courant")
async def me(ctx: Auth, db: Db) -> s.MeOut:
    return await build_me(db, ctx)


@router.patch("/me/profile", response_model=s.MeOut, summary="Modifier le profil")
async def update_profile(body: s.ProfileIn, ctx: Auth, db: Db) -> s.MeOut:
    if body.display_name is not None:
        await db.execute(
            text("UPDATE profiles SET display_name = :n WHERE user_id = :u"),
            {"n": body.display_name, "u": ctx.user_id},
        )
    if body.locale is not None:
        await db.execute(text("UPDATE users SET locale = :l WHERE id = :u"), {"l": body.locale, "u": ctx.user_id})
        ctx.locale = body.locale
    await db.commit()
    return await build_me(db, ctx)


def _ip_hint(ip: str | None) -> str | None:
    if not ip:
        return None
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + ":…"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".x" if len(parts) == 4 else None


@router.get("/me/sessions", response_model=list[s.DeviceSessionOut], summary="Sessions actives")
async def my_sessions(ctx: Auth, db: Db, settings: SettingsDep) -> list[s.DeviceSessionOut]:
    rows = (
        await db.execute(
            text(
                "SELECT id, family_id, created_at, last_seen_at, user_agent, host(ip) AS ip FROM auth_sessions "
                "WHERE user_id = :u AND revoked_at IS NULL AND expires_at > now() "
                "AND last_seen_at > now() - make_interval(mins => :idle) ORDER BY last_seen_at DESC"
            ),
            {"u": ctx.user_id, "idle": settings.session_idle_timeout_minutes},
        )
    ).all()
    return [
        s.DeviceSessionOut(
            id=r.id,
            created_at=r.created_at,
            last_seen_at=r.last_seen_at,
            user_agent=(r.user_agent or "")[:120] or None,
            ip_hint=_ip_hint(r.ip),
            current=r.family_id == ctx.family_id,
        )
        for r in rows
    ]


@router.delete("/me/sessions/{session_id}", status_code=204, summary="Fermer une session")
async def revoke_session(session_id: str, request: Request, ctx: Auth, db: Db) -> Response:
    sid = parse_uuid(session_id)
    family = (
        await db.execute(
            text("SELECT family_id FROM auth_sessions WHERE id = :id AND user_id = :u AND revoked_at IS NULL"),
            {"id": sid, "u": ctx.user_id},
        )
    ).scalar()
    if family is None:  # inconnue ou appartenant à quelqu'un d'autre : même réponse
        raise ProblemError(404, "Session introuvable", type_=PROBLEM_BASE + "not-found")
    await revoke_family(db, family, "logout")
    await audit(db, "session.revoked", "auth_session", sid, actor=ctx.user_id, ip=client_ip(request))
    await db.commit()
    return Response(status_code=204)

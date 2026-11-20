"""Identité et accès (Phase 2.1) : inscription, vérification, sessions, mot de passe, RBAC,
invitations, isolation des établissements et audit — contre PostgreSQL (et Redis s'il est présent).

Les courriels sont capturés en mémoire (MAIL_BACKEND=memory) ; le test Mailpit réel est à part.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.main import create_app

PASSWORD = "Une phrase de passe solide 2026"
NEW_PASSWORD = "Encore une autre phrase solide 42"
TOKEN_RE = re.compile(r"jeton=([A-Za-z0-9_-]{20,})")


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "cors_origins": ["http://localhost:3000"],
        "rate_limit_salt": uuid.uuid4().hex,
        "rate_limit_per_minute": 0,
        "mail_backend": "memory",
        "register_per_hour_per_ip": 10_000,
        "login_per_10min_per_ip": 10_000,
        "email_actions_per_hour_per_ip": 10_000,
        "email_actions_per_hour_per_address": 10_000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def api() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


@dataclass
class Account:
    email: str
    token: str
    user_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def outbox(c: TestClient) -> list:
    return c.app.state.mailer.outbox  # type: ignore[attr-defined]


def last_token_for(c: TestClient, email: str) -> str:
    for mail in reversed(outbox(c)):
        if mail.to == email:
            match = TOKEN_RE.search(mail.body)
            if match:
                return match.group(1)
    raise AssertionError(f"aucun lien reçu par {email}")


def email() -> str:
    return f"etu-{uuid.uuid4().hex[:10]}@example.test"


def register(c: TestClient, address: str, **extra: object) -> object:
    body = {
        "email": address,
        "password": PASSWORD,
        "display_name": "Étudiante Test",
        "locale": "fr",
        "adult_attestation": True,
        "accept_terms": True,
        "accept_privacy": True,
    }
    body.update(extra)
    return c.post("/api/v1/auth/register", json=body)


def login(c: TestClient, address: str, password: str = PASSWORD):  # noqa: ANN201
    return c.post("/api/v1/auth/login", json={"email": address, "password": password})


def make_account(c: TestClient, address: str | None = None) -> Account:
    address = address or email()
    assert register(c, address).status_code == 202
    assert c.post("/api/v1/auth/verify-email", json={"token": last_token_for(c, address)}).status_code == 200
    r = login(c, address)
    assert r.status_code == 200, r.text
    return Account(address, r.json()["session_token"], r.json()["user"]["id"])


def sql(engine: Engine, query: str, **params: object):  # noqa: ANN201
    with engine.begin() as conn:
        return conn.execute(text(query), params)


def grant(engine: Engine, user_id: str, role: str, org: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('nl.role_grant_bypass', 'on', true)"))
        conn.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id, organization_id) SELECT :u, id, :o FROM roles WHERE code=:r"
            ),
            {"u": user_id, "o": org, "r": role},
        )


def make_org(engine: Engine) -> str:
    tag = uuid.uuid4().hex[:8]
    return str(
        sql(
            engine,
            "INSERT INTO organizations (slug, name, kind) VALUES (:s, :n, 'school') RETURNING id",
            s=f"org-{tag}",
            n=f"Institut {tag}",
        ).scalar()
    )


def relogin(c: TestClient, acct: Account) -> Account:
    r = login(c, acct.email)
    assert r.status_code == 200, r.text
    return Account(acct.email, r.json()["session_token"], acct.user_id)


# --------------------------------------------------------------------------------------------
# Inscription et vérification
# --------------------------------------------------------------------------------------------
def test_register_creates_pending_student_and_sends_local_email(api, sync_engine):
    address = email()
    r = register(api, address)
    assert r.status_code == 202 and r.json() == {"status": "accepted"}
    row = sql(
        sync_engine,
        "SELECT u.status, u.adult_attested_at IS NOT NULL AS adult, u.terms_version, u.privacy_policy_version, "
        "u.password_hash LIKE '$argon2id$%' AS argon, array_agg(r.code) AS roles FROM users u "
        "JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id WHERE u.email = :e "
        "GROUP BY u.id",
        e=address,
    ).one()
    assert row.status == "pending_verification" and row.adult and row.argon
    assert row.terms_version and row.privacy_policy_version
    assert row.roles == ["student"]
    assert last_token_for(api, address)
    # Connexion impossible avant vérification.
    assert login(api, address).json()["type"].endswith("email-not-verified")


def test_register_rejects_role_fields_and_never_grants_privileges(api, sync_engine):
    for extra in ({"role": "admin"}, {"roles": ["medical_reviewer"]}, {"organization_id": str(uuid.uuid4())}):
        assert register(api, email(), **extra).status_code == 422


def test_register_requires_adult_and_consents(api):
    for field in ("adult_attestation", "accept_terms", "accept_privacy"):
        r = register(api, email(), **{field: False})
        assert r.status_code == 422 and field in r.json()["missing"]


@pytest.mark.parametrize(
    ("password", "code"),
    [("court", "password_too_short"), ("motdepasse123", "password_too_common"), ("x" * 200, "password_too_long")],
)
def test_password_policy(api, password, code):
    r = register(api, email(), password=password)
    assert r.status_code == 422 and code in r.json()["password_errors"]


def test_password_containing_email_is_refused(api):
    r = register(api, "camille.dupont@example.test", password="camille.dupont-2026-ok")
    assert "password_contains_email" in r.json()["password_errors"]


def test_honeypot_creates_nothing(api, sync_engine):
    address = email()
    assert register(api, address, website="http://spam.example").status_code == 202
    assert sql(sync_engine, "SELECT count(*) FROM users WHERE email = :e", e=address).scalar() == 0


def test_duplicate_registration_reveals_nothing(api, sync_engine):
    acct = make_account(api)
    first = register(api, email())
    again = register(api, acct.email)
    assert again.status_code == first.status_code == 202 and again.json() == first.json()
    assert sql(sync_engine, "SELECT count(*) FROM users WHERE email = :e", e=acct.email).scalar() == 1
    assert any(m.to == acct.email and "compte existe déjà" in m.body for m in outbox(api))


def test_verification_token_single_use_and_expiry(api, sync_engine):
    address = email()
    register(api, address)
    token = last_token_for(api, address)
    assert api.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
    assert api.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400
    other = email()
    register(api, other)
    sql(
        sync_engine,
        "UPDATE one_time_tokens SET created_at = now() - interval '3 days', expires_at = now() - interval '1 hour' "
        "WHERE user_id = (SELECT id FROM users WHERE email = :e)",
        e=other,
    )
    assert api.post("/api/v1/auth/verify-email", json={"token": last_token_for(api, other)}).status_code == 400


def test_resend_verification_is_uniform_and_invalidates_previous_link(api):
    address = email()
    register(api, address)
    first = last_token_for(api, address)
    assert api.post("/api/v1/auth/resend-verification", json={"email": address}).status_code == 202
    assert api.post("/api/v1/auth/resend-verification", json={"email": email()}).status_code == 202
    second = last_token_for(api, address)
    assert first != second
    assert api.post("/api/v1/auth/verify-email", json={"token": first}).status_code == 400
    assert api.post("/api/v1/auth/verify-email", json={"token": second}).status_code == 200


# --------------------------------------------------------------------------------------------
# Connexion, sessions, révocation
# --------------------------------------------------------------------------------------------
def test_login_failures_are_uniform_and_lock_the_account(sync_engine):
    with TestClient(create_app(_settings(login_max_failures=3))) as c:
        acct = make_account(c)
        unknown = login(c, email(), "peu importe 123456")
        wrong = login(c, acct.email, "mauvais mot de passe 1")
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]
        login(c, acct.email, "mauvais mot de passe 2")
        login(c, acct.email, "mauvais mot de passe 3")  # 3e échec : verrouillage
        assert login(c, acct.email).status_code == 401  # même le bon mot de passe est refusé
        assert any(m.to == acct.email and "bloquées" in m.body for m in outbox(c))
        sql(sync_engine, "UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e", e=acct.email)
        assert login(c, acct.email).status_code == 200


def test_session_is_bearer_only_and_logout_revokes(api):
    acct = make_account(api)
    me = api.get("/api/v1/me", headers=acct.headers)
    assert me.status_code == 200 and me.json()["email"] == acct.email
    assert [r["role"] for r in me.json()["roles"]] == ["student"]
    assert "password_hash" not in me.text
    assert api.get("/api/v1/me").status_code == 401
    # Un cookie n'authentifie pas l'API : aucune requête intersite ne peut agir en son nom (CSRF).
    assert api.get("/api/v1/me", cookies={"nl_session": acct.token}).status_code == 401
    assert api.post("/api/v1/auth/logout", cookies={"nl_session": acct.token}).status_code == 401
    assert api.post("/api/v1/auth/logout", headers=acct.headers).status_code == 204
    assert api.get("/api/v1/me", headers=acct.headers).status_code == 401


def test_idle_and_absolute_expiry(api, sync_engine):
    idle = make_account(api)
    sql(
        sync_engine,
        "UPDATE auth_sessions SET last_seen_at = now() - interval '3 hours' WHERE user_id = :u AND revoked_at IS NULL",
        u=idle.user_id,
    )
    assert api.get("/api/v1/me", headers=idle.headers).status_code == 401
    absolute = make_account(api)
    sql(
        sync_engine,
        "UPDATE auth_sessions SET created_at = now() - interval '8 days', expires_at = now() - interval '1 minute' "
        "WHERE user_id = :u AND revoked_at IS NULL",
        u=absolute.user_id,
    )
    assert api.get("/api/v1/me", headers=absolute.headers).status_code == 401


def test_sensitive_operations_require_activity_within_30_minutes(api, sync_engine):
    acct = make_account(api)
    assert api.get("/api/v1/me", headers=acct.headers).json()["sensitive_fresh"] is True
    sql(
        sync_engine,
        "UPDATE auth_sessions SET last_seen_at = now() - interval '31 minutes' WHERE user_id = :u AND revoked_at IS NULL",
        u=acct.user_id,
    )
    me = api.get("/api/v1/me", headers=acct.headers)
    assert me.status_code == 200 and me.json()["sensitive_fresh"] is False  # session valide, fraîcheur perdue
    denied = api.post("/api/v1/auth/logout-all", headers=acct.headers)
    assert denied.status_code == 403 and denied.json()["type"].endswith("reauthentication-required")
    assert (
        api.post(
            "/api/v1/auth/reauthenticate", headers=acct.headers, json={"password": "faux mot de passe 1"}
        ).status_code
        == 401
    )
    fresh = api.post("/api/v1/auth/reauthenticate", headers=acct.headers, json={"password": PASSWORD})
    assert fresh.status_code == 200
    renewed = Account(acct.email, fresh.json()["session_token"], acct.user_id)
    other_device = relogin(api, acct)
    assert api.post("/api/v1/auth/logout-all", headers=renewed.headers).status_code == 204
    for a in (renewed, other_device):
        assert api.get("/api/v1/me", headers=a.headers).status_code == 401


def test_rotation_grace_and_reuse_detection(api, sync_engine):
    acct = make_account(api)
    assert api.post("/api/v1/auth/session/refresh", headers=acct.headers).json()["rotated"] is False
    sql(
        sync_engine,
        "UPDATE auth_sessions SET created_at = now() - interval '16 minutes' WHERE user_id = :u AND revoked_at IS NULL",
        u=acct.user_id,
    )
    r = api.post("/api/v1/auth/session/refresh", headers=acct.headers)
    assert r.json()["rotated"] is True
    new = Account(acct.email, r.json()["session_token"], acct.user_id)
    assert new.token != acct.token
    # Requête parallèle juste après la rotation : tolérée.
    assert api.get("/api/v1/me", headers=acct.headers).status_code == 200
    # Hors délai de grâce : réutilisation = vol présumé, toute la famille est révoquée.
    sql(
        sync_engine,
        "UPDATE auth_sessions SET rotated_at = now() - interval '5 minutes' WHERE user_id = :u AND rotated_at IS NOT NULL",
        u=acct.user_id,
    )
    assert api.get("/api/v1/me", headers=acct.headers).status_code == 401
    assert api.get("/api/v1/me", headers=new.headers).status_code == 401
    assert (
        sql(
            sync_engine,
            "SELECT count(*) FROM audit_logs WHERE action = 'auth.session_reuse' AND actor_user_id = :u",
            u=acct.user_id,
        ).scalar()
        == 1
    )


def test_list_and_close_own_sessions_only(api):
    acct = make_account(api)
    other = relogin(api, acct)
    sessions = api.get("/api/v1/me/sessions", headers=acct.headers).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    victim = make_account(api)
    victim_session = api.get("/api/v1/me/sessions", headers=victim.headers).json()[0]["id"]
    assert api.delete(f"/api/v1/me/sessions/{victim_session}", headers=acct.headers).status_code == 404
    target = next(s["id"] for s in sessions if not s["current"])
    assert api.delete(f"/api/v1/me/sessions/{target}", headers=acct.headers).status_code == 204
    assert api.get("/api/v1/me", headers=other.headers).status_code == 401
    assert api.get("/api/v1/me", headers=victim.headers).status_code == 200


# --------------------------------------------------------------------------------------------
# Mot de passe
# --------------------------------------------------------------------------------------------
def test_password_reset_flow(api, sync_engine):
    acct = make_account(api)
    second_device = relogin(api, acct)
    before = len(outbox(api))
    assert api.post("/api/v1/auth/password/forgot", json={"email": email()}).status_code == 202
    assert len(outbox(api)) == before  # adresse inconnue : rien n'est envoyé, réponse identique
    assert api.post("/api/v1/auth/password/forgot", json={"email": acct.email}).status_code == 202
    token = last_token_for(api, acct.email)
    weak = api.post("/api/v1/auth/password/reset", json={"token": token, "new_password": "court"})
    assert weak.status_code == 422  # le jeton reste valable
    assert (
        api.post("/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200
    )
    assert (
        api.post("/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 400
    )
    for a in (acct, second_device):
        assert api.get("/api/v1/me", headers=a.headers).status_code == 401
    assert login(api, acct.email).status_code == 401
    assert login(api, acct.email, NEW_PASSWORD).status_code == 200
    assert any(m.to == acct.email and "modifié" in m.subject for m in outbox(api))


def test_password_reset_token_expires(api, sync_engine):
    acct = make_account(api)
    api.post("/api/v1/auth/password/forgot", json={"email": acct.email})
    token = last_token_for(api, acct.email)
    sql(
        sync_engine,
        "UPDATE one_time_tokens SET created_at = now() - interval '2 hours', expires_at = now() - interval '1 minute' "
        "WHERE user_id = :u AND purpose = 'password_reset'",
        u=acct.user_id,
    )
    assert (
        api.post("/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 400
    )


def test_change_password_needs_current_password_and_closes_other_sessions(api):
    acct = make_account(api)
    other = relogin(api, acct)
    wrong = api.post(
        "/api/v1/auth/password/change",
        headers=acct.headers,
        json={"current_password": "pas le bon 123456", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 401
    ok = api.post(
        "/api/v1/auth/password/change",
        headers=acct.headers,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert ok.status_code == 200
    assert api.get("/api/v1/me", headers=other.headers).status_code == 401
    current = Account(acct.email, ok.json()["session_token"], acct.user_id)
    assert api.get("/api/v1/me", headers=current.headers).status_code == 200


# --------------------------------------------------------------------------------------------
# RBAC, invitations et isolation des établissements
# --------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(api, sync_engine):
    """Administrateur de la plateforme, deux établissements, un administrateur d'établissement pour A."""
    admin = make_account(api)
    grant(sync_engine, admin.user_id, "admin")
    admin = relogin(api, admin)
    org_a, org_b = make_org(sync_engine), make_org(sync_engine)
    # Programme privé de l'établissement B (brouillon), pour vérifier l'isolation des données.
    sql(
        sync_engine,
        "INSERT INTO programs (code, profession_id, title_i18n, level, access_tier, status, organization_id) "
        'SELECT :c, p.id, \'{"fr":"Programme privé B","en":"Private program B"}\', \'beginner\', \'organization\', '
        "'draft', :o FROM professions p JOIN jurisdictions j ON j.id = p.jurisdiction_id "
        "WHERE j.code = 'CA-QC' AND p.code = 'pab'",
        c=f"prive-b-{uuid.uuid4().hex[:6]}",
        o=org_b,
    )
    org_admin = invite_and_accept(api, admin, "org_admin", org_a)
    return {"admin": admin, "org_a": org_a, "org_b": org_b, "org_admin": org_admin}


def invite(c: TestClient, inviter: Account, address: str, role: str, org: str | None):  # noqa: ANN201
    return c.post(
        "/api/v1/invitations",
        headers=inviter.headers,
        json={"email": address, "role": role, "organization_id": org},
    )


def invite_and_accept(c: TestClient, inviter: Account, role: str, org: str | None) -> Account:
    address = email()
    r = invite(c, inviter, address, role, org)
    assert r.status_code == 201, r.text
    assert "token" not in r.text
    invitation_token = last_token_for(c, address)
    member = make_account(c, address)
    accepted = c.post("/api/v1/invitations/accept", headers=member.headers, json={"token": invitation_token})
    assert accepted.status_code == 200, accepted.text
    return Account(address, accepted.json()["session_token"], member.user_id)


def test_admin_invitation_grants_role_with_audit(api, sync_engine, world):
    me = api.get("/api/v1/me", headers=world["org_admin"].headers).json()
    assert {"role": "org_admin", "organization_id": world["org_a"]}.items() <= next(
        r for r in me["roles"] if r["role"] == "org_admin"
    ).items()
    actions = {
        a
        for (a,) in sql(
            sync_engine,
            "SELECT action FROM audit_logs WHERE organization_id = :o",
            o=world["org_a"],
        ).all()
    }
    assert {"invitation.created", "invitation.accepted", "role.granted"} <= actions


def test_org_admin_least_privilege(api, world):
    org_admin, org_a, org_b = world["org_admin"], world["org_a"], world["org_b"]
    instructor = invite_and_accept(api, org_admin, "instructor", org_a)
    assert any(r["role"] == "instructor" for r in api.get("/api/v1/me", headers=instructor.headers).json()["roles"])
    assert invite(api, org_admin, email(), "instructor", org_b).status_code == 404  # autre établissement
    assert invite(api, org_admin, email(), "org_admin", org_a).status_code == 403
    assert invite(api, org_admin, email(), "medical_reviewer", None).status_code == 403
    assert invite(api, org_admin, email(), "instructor", None).status_code == 403  # rôle global
    for role in ("admin", "student"):
        assert invite(api, org_admin, email(), role, org_a).status_code == 422  # rôles non invitables


def test_student_cannot_escalate(api, world):
    student = make_account(api)
    assert invite(api, student, student.email, "instructor", None).status_code == 403
    assert invite(api, student, student.email, "org_admin", world["org_a"]).status_code == 404
    assert api.get("/api/v1/invitations", headers=student.headers).status_code == 404
    assert (
        api.delete(f"/api/v1/admin/users/{student.user_id}/roles/student", headers=student.headers).status_code == 404
    )
    assert api.patch("/api/v1/me/profile", headers=student.headers, json={"roles": ["admin"]}).status_code == 422
    assert [r["role"] for r in api.get("/api/v1/me", headers=student.headers).json()["roles"]] == ["student"]


def test_invitation_bound_to_email_single_use_expiry_and_revocation(api, sync_engine, world):
    admin, org_a = world["admin"], world["org_a"]
    target = email()
    assert invite(api, admin, target, "instructor", org_a).status_code == 201
    token = last_token_for(api, target)
    intruder = make_account(api)
    assert api.post("/api/v1/invitations/accept", headers=intruder.headers, json={"token": token}).status_code == 403
    assert invite(api, admin, target, "instructor", org_a).status_code == 409  # déjà en attente
    owner = make_account(api, target)
    assert api.post("/api/v1/invitations/accept", headers=owner.headers, json={"token": token}).status_code == 200
    assert api.post("/api/v1/invitations/accept", headers=owner.headers, json={"token": token}).status_code == 400
    # Expirée
    late = email()
    invite(api, admin, late, "instructor", org_a)
    late_token = last_token_for(api, late)
    sql(
        sync_engine,
        "UPDATE invitations SET created_at = now() - interval '4 days', expires_at = now() - interval '1 hour' "
        "WHERE email = :e",
        e=late,
    )
    late_acct = make_account(api, late)
    assert (
        api.post("/api/v1/invitations/accept", headers=late_acct.headers, json={"token": late_token}).status_code == 400
    )
    # Révoquée
    revoked = email()
    created = invite(api, admin, revoked, "instructor", org_a).json()
    revoked_token = last_token_for(api, revoked)
    assert api.delete(f"/api/v1/invitations/{created['id']}", headers=admin.headers).status_code == 204
    revoked_acct = make_account(api, revoked)
    assert (
        api.post("/api/v1/invitations/accept", headers=revoked_acct.headers, json={"token": revoked_token}).status_code
        == 400
    )


def test_invitation_rejected_when_inviter_lost_rights(api, sync_engine, world):
    temp_admin = invite_and_accept(api, world["admin"], "org_admin", world["org_a"])
    target = email()
    assert invite(api, temp_admin, target, "instructor", world["org_a"]).status_code == 201
    token = last_token_for(api, target)
    sql(sync_engine, "DELETE FROM user_roles WHERE user_id = :u", u=temp_admin.user_id)
    acct = make_account(api, target)
    assert api.post("/api/v1/invitations/accept", headers=acct.headers, json={"token": token}).status_code == 400


def test_organization_isolation(api, world):
    org_admin, org_a, org_b = world["org_admin"], world["org_a"], world["org_b"]
    h = org_admin.headers
    assert [o["id"] for o in api.get("/api/v1/organizations/mine", headers=h).json()] == [org_a]
    for path in ("members", "invitations", "programs"):
        assert api.get(f"/api/v1/organizations/{org_a}/{path}", headers=h).status_code == 200
        assert api.get(f"/api/v1/organizations/{org_b}/{path}", headers=h).status_code == 404
        assert api.get(f"/api/v1/organizations/{uuid.uuid4()}/{path}", headers=h).status_code == 404
        assert api.get(f"/api/v1/organizations/pas-un-uuid/{path}", headers=h).status_code == 404
    # Données privées de B invisibles pour A, visibles pour l'administrateur de la plateforme.
    assert api.get(f"/api/v1/organizations/{org_a}/programs", headers=h).json() == []
    admin_view = api.get(f"/api/v1/organizations/{org_b}/programs", headers=world["admin"].headers).json()
    assert [p["title"] for p in admin_view] == ["Programme privé B"]
    # Modifications hors périmètre : invitation de B, rôle d'un membre de B.
    b_member = invite_and_accept(api, world["admin"], "instructor", org_b)
    b_invite = invite(api, world["admin"], email(), "instructor", org_b).json()
    assert api.delete(f"/api/v1/invitations/{b_invite['id']}", headers=h).status_code == 404
    assert (
        api.delete(f"/api/v1/organizations/{org_b}/members/{b_member.user_id}/roles/instructor", headers=h).status_code
        == 404
    )
    assert api.get("/api/v1/me", headers=b_member.headers).status_code == 200
    # Les membres listés de A ne contiennent personne de B.
    members_a = {m["user_id"] for m in api.get(f"/api/v1/organizations/{org_a}/members", headers=h).json()}
    assert b_member.user_id not in members_a
    # Liste globale des établissements : réservée à l'administrateur de la plateforme.
    assert api.get("/api/v1/admin/organizations", headers=h).status_code == 404
    assert api.get("/api/v1/admin/organizations", headers=b_member.headers).status_code == 404
    listed = {o["id"] for o in api.get("/api/v1/admin/organizations", headers=world["admin"].headers).json()}
    assert {org_a, org_b} <= listed


def test_role_revocation_closes_sessions_and_is_audited(api, sync_engine, world):
    instructor = invite_and_accept(api, world["org_admin"], "instructor", world["org_a"])
    path = f"/api/v1/organizations/{world['org_a']}/members/{instructor.user_id}/roles/instructor"
    assert api.delete(path, headers=world["org_admin"].headers).status_code == 204
    assert api.get("/api/v1/me", headers=instructor.headers).status_code == 401
    roles = [r["role"] for r in api.get("/api/v1/me", headers=relogin(api, instructor).headers).json()["roles"]]
    assert roles == ["student"]
    assert (
        sql(
            sync_engine,
            "SELECT count(*) FROM audit_logs WHERE action = 'role.revoked' AND entity_id = :u",
            u=instructor.user_id,
        ).scalar()
        == 1
    )


# --------------------------------------------------------------------------------------------
# Audit et confidentialité
# --------------------------------------------------------------------------------------------
def test_audit_never_contains_secrets(api, sync_engine):
    acct = make_account(api)
    api.post("/api/v1/auth/password/forgot", json={"email": acct.email})
    reset_token = last_token_for(api, acct.email)
    login(api, acct.email, "mauvais mot de passe 9")
    rows = sql(
        sync_engine,
        "SELECT action, metadata::text AS meta, coalesce(entity_id, '') AS eid FROM audit_logs "
        "WHERE actor_user_id = :u OR entity_id = :uid",
        u=acct.user_id,
        uid=acct.user_id,
    ).all()
    blob = " ".join(f"{r.action} {r.meta} {r.eid}" for r in rows)
    assert {"user.register", "user.email_verified", "auth.login", "password.reset_requested", "auth.login_failed"} <= {
        r.action for r in rows
    }
    for secret in (PASSWORD, "mauvais mot de passe 9", acct.token, reset_token, acct.email):
        assert secret not in blob


def test_tokens_are_stored_as_hmac_only(api, sync_engine):
    acct = make_account(api)
    hashes = [
        h
        for (h,) in sql(sync_engine, "SELECT refresh_token_hash FROM auth_sessions WHERE user_id = :u", u=acct.user_id)
    ]
    assert acct.token not in hashes and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)


# --------------------------------------------------------------------------------------------
# Limites anti-abus
# --------------------------------------------------------------------------------------------
def test_registration_rate_limit_per_ip():
    if not os.environ.get("REDIS_URL"):
        pytest.skip("Redis absent : limites d'authentification vérifiées en CI et dans Docker")
    with TestClient(create_app(_settings(register_per_hour_per_ip=2))) as c:
        statuses = [register(c, email()).status_code for _ in range(3)]
    assert statuses == [202, 202, 429]


def test_forgot_password_rate_limit_per_address_is_silent():
    if not os.environ.get("REDIS_URL"):
        pytest.skip("Redis absent")
    with TestClient(create_app(_settings(email_actions_per_hour_per_address=1))) as c:
        acct = make_account(c)
        before = len(outbox(c))
        statuses = [c.post("/api/v1/auth/password/forgot", json={"email": acct.email}).status_code for _ in range(3)]
        assert statuses == [202, 202, 202]  # réponse identique, mais un seul courriel envoyé
        assert len(outbox(c)) == before + 1

"""Migration 0004 : garde d'attribution des rôles et invitations (défense en profondeur, ADR-0007)."""

from __future__ import annotations

import uuid


def _grant_sql(user: str, role: str, org: str | None, granter: str | None) -> str:
    org_sql = f"'{org}'" if org else "NULL"
    granter_sql = f"'{granter}'" if granter else "NULL"
    return (
        "INSERT INTO user_roles (user_id, role_id, organization_id, granted_by) "
        f"SELECT '{user}', id, {org_sql}, {granter_sql} FROM roles WHERE code = '{role}'"
    )


def _invite_sql(email: str, role: str, org: str | None, inviter: str | None) -> str:
    org_sql = f"'{org}'" if org else "NULL"
    inviter_sql = f"'{inviter}'" if inviter else "NULL"
    return (
        "INSERT INTO invitations (email, role_id, organization_id, invited_by, token_hash, expires_at) "
        f"SELECT '{email}', id, {org_sql}, {inviter_sql}, '{uuid.uuid4().hex}', now() + interval '1 day' "
        f"FROM roles WHERE code = '{role}'"
    )


def test_student_role_needs_no_granter(db, make_user):
    db.sql(_grant_sql(make_user(), "student", None, None))


def test_privileged_role_without_granter_is_refused(db, make_user):
    for role in ("instructor", "medical_reviewer", "org_admin", "admin"):
        assert "refusée" in db.fails(_grant_sql(make_user(), role, None, None))


def test_self_grant_is_refused_even_for_admin(db, make_user, grant_role):
    admin = make_user()
    grant_role(admin, "admin")
    assert "refusée" in db.fails(_grant_sql(admin, "medical_reviewer", None, admin))


def test_platform_admin_can_grant_privileged_roles(db, make_user, grant_role, make_org):
    admin = make_user()
    grant_role(admin, "admin")
    db.sql(_grant_sql(make_user(), "medical_reviewer", None, admin))
    db.sql(_grant_sql(make_user(), "org_admin", make_org(), admin))


def test_org_admin_limited_to_instructor_in_own_org(db, make_user, grant_role, make_org):
    org_a, org_b = make_org(), make_org()
    org_admin = make_user()
    grant_role(org_admin, "org_admin", org_a)
    db.sql(_grant_sql(make_user(), "instructor", org_a, org_admin))
    # Autre établissement, rôle global, rôles non délégables : refusés.
    assert "refusée" in db.fails(_grant_sql(make_user(), "instructor", org_b, org_admin))
    assert "refusée" in db.fails(_grant_sql(make_user(), "instructor", None, org_admin))
    for role in ("medical_reviewer", "org_admin", "admin"):
        assert "refusée" in db.fails(_grant_sql(make_user(), role, org_a, org_admin))


def test_role_update_cannot_escalate(db, make_user, grant_role, make_org):
    org_a = make_org()
    org_admin = make_user()
    grant_role(org_admin, "org_admin", org_a)
    member = make_user()
    db.sql(_grant_sql(member, "instructor", org_a, org_admin))
    assert "refusée" in db.fails(
        f"UPDATE user_roles SET role_id = (SELECT id FROM roles WHERE code='admin') WHERE user_id = '{member}'"
    )


def test_invitation_rules(db, make_user, grant_role, make_org):
    admin = make_user()
    grant_role(admin, "admin")
    org_a, org_b = make_org(), make_org()
    for role in ("student", "admin"):
        assert "invitation" in db.fails(_invite_sql("x@example.test", role, None, admin))
    assert "établissement" in db.fails(_invite_sql("y@example.test", "org_admin", None, admin))
    assert "global" in db.fails(_invite_sql("z@example.test", "medical_reviewer", org_a, admin))
    db.sql(_invite_sql("rev@example.test", "medical_reviewer", None, admin))
    org_admin = make_user()
    grant_role(org_admin, "org_admin", org_a)
    db.sql(_invite_sql("inst@example.test", "instructor", org_a, org_admin))
    assert "non habilité" in db.fails(_invite_sql("i2@example.test", "instructor", org_b, org_admin))
    assert "non habilité" in db.fails(_invite_sql("i3@example.test", "org_admin", org_a, org_admin))


def test_single_pending_invitation_per_person_role_org(db, make_user, grant_role, make_org):
    admin = make_user()
    grant_role(admin, "admin")
    org = make_org()
    db.sql(_invite_sql("dup@example.test", "instructor", org, admin))
    assert "ux_invitations_pending" in db.fails(_invite_sql("DUP@example.test", "instructor", org, admin))


def test_one_time_tokens_constraints(db, make_user):
    user = make_user()
    assert (
        "check"
        in db.fails(
            "INSERT INTO one_time_tokens (user_id, purpose, token_hash, expires_at) "
            f"VALUES ('{user}', 'login', 'h1', now() + interval '1 hour')"
        ).lower()
    )
    db.sql(
        "INSERT INTO one_time_tokens (user_id, purpose, token_hash, expires_at) "
        f"VALUES ('{user}', 'password_reset', 'h-{user}', now() + interval '1 hour')"
    )


def test_audit_log_is_append_only(db):
    db.sql("INSERT INTO audit_logs (action, entity_type, entity_id, metadata) VALUES ('auth.login', 'user', 'x', '{}')")
    assert db.fails("UPDATE audit_logs SET action = 'auth.logout'")
    assert db.fails("DELETE FROM audit_logs")

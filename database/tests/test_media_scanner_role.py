"""Migration 0008 — rôle de service media_scanner et connexion PostgreSQL nl_media_scanner (2.2.3-c).

Deux identités distinctes sont exigées pour tout verdict antivirus :
- la CONNEXION PostgreSQL `nl_media_scanner` (session_user, authentifiée par mot de passe) ;
- l'ACTEUR métier déclaré = compte de service, seul titulaire du rôle `media_scanner`.
Comptes et contenus FICTIFS ; mot de passe de test aléatoire, jamais versionné.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess

import pytest
from conftest import SCHEMA, Db, SqlError, build_db, new_id
from editorial import EditorialV2, q
from test_media_foundation import Media, scan_event_sql
from test_program_versions import World, _signature

UP = SCHEMA / "0008_media_scanner_role.sql"
DOWN = SCHEMA / "0008_media_scanner_role_down.sql"
UP7 = SCHEMA / "0007_secure_media_foundation.sql"
ACCOUNT = "00000000-0000-4000-a000-00000000c1a5"


class ScannerDb(Db):
    """Même base, mais connexion ouverte par le rôle PostgreSQL nl_media_scanner."""

    def __init__(self, name: str, password: str):
        super().__init__(name)
        self.base[self.base.index("-U") + 1] = "nl_media_scanner"
        self.password = password

    def _run(self, args: list[str], db: str | None = None) -> str:
        env = {**os.environ, "PGPASSWORD": self.password}
        proc = subprocess.run(self.base + ["-d", db or self.name] + args, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise SqlError(proc.stderr.strip())
        return proc.stdout.strip()


def scanner_login(d: Db) -> ScannerDb:
    """Connexion de service. Sur un cluster partagé (pile Docker de développement), le mot de passe de
    l'exploitation est fourni (NL_TEST_SCANNER_PASSWORD) et n'est jamais modifié ; sinon (CI, base
    locale), un mot de passe aléatoire est posé comme le ferait l'exploitation."""
    password = os.environ.get("NL_TEST_SCANNER_PASSWORD", "")
    if not password:
        password = secrets.token_urlsafe(24)
        d.admin(f"ALTER ROLE nl_media_scanner LOGIN PASSWORD '{password}'")
    return ScannerDb(d.name, password)


class Scanner:
    """Transactions de la connexion de service, avec l'acteur choisi (le compte de service par défaut)."""

    def __init__(self, sd: ScannerDb):
        self.sd = sd
        self.ed = EditorialV2(sd)

    def run(self, sql: str, actor: str = ACCOUNT) -> str:
        return self.ed.run(actor, sql)

    def fails(self, sql: str, actor: str | None = ACCOUNT) -> str:
        return self.ed.fails(actor, sql)

    def start(self, mid: str, attempt: int = 1) -> None:
        self.run(f"UPDATE media_assets SET state = 'scanning', scan_attempt = {attempt} WHERE id = '{mid}'")


@pytest.fixture
def sc(db) -> Scanner:
    return Scanner(scanner_login(db))


@pytest.fixture
def w(db) -> World:
    return World(db)


@pytest.fixture
def m(db) -> Media:
    return Media(db)


@pytest.fixture
def scratch():
    created: list[Db] = []

    def _make(tag: str, upto: str = "0008_media_scanner_role", seeds: bool = True) -> Db:
        d = build_db(f"nl_m8_{tag}_{os.getpid()}", upto=upto, seeds=seeds)
        created.append(d)
        return d

    yield _make
    for d in created:
        d.admin(f"DROP DATABASE IF EXISTS {d.name} WITH (FORCE)")


def to_clean(mid: str) -> str:
    return f"UPDATE media_assets SET state = 'clean', clean_key = '{new_id()}/{new_id()}' WHERE id = '{mid}'"


# =============================================================================================
# 1. Rôle métier, compte de service, rôle de connexion : privilèges minimaux
# =============================================================================================
def test_business_role_and_service_account(db):
    assert (
        db.sql(
            "SELECT string_agg(permission_code, ',') FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "WHERE r.code = 'media_scanner'"
        )
        == "media:scan"
    )
    # media:scan n'est détenue par AUCUN autre rôle (ni admin, ni relecteur, ni établissement).
    assert (
        db.sql(
            "SELECT string_agg(r.code, ',') FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "WHERE rp.permission_code = 'media:scan'"
        )
        == "media_scanner"
    )
    # Aucun droit de publication, d'administration, de consultation d'apprenant.
    assert (
        db.sql(
            "SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id WHERE r.code = 'media_scanner' "
            "AND rp.permission_code IN ('content:publish','content:review','user:manage','org:manage','catalog:read','learning:participate')"
        )
        == "0"
    )
    row = db.sql(f"SELECT status || '|' || (password_hash IS NULL) || '|' || email FROM users WHERE id = '{ACCOUNT}'")
    assert row == "active|true|media-scanner@service.nurselearn.invalid"
    assert (
        db.sql(
            f"SELECT string_agg(r.code || ':' || coalesce(ur.organization_id::text, 'global'), ',') FROM user_roles ur "
            f"JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = '{ACCOUNT}'"
        )
        == "media_scanner:global"
    )


def test_postgres_login_role_is_not_privileged(db):
    attrs = db.sql(
        "SELECT rolsuper || '|' || rolcreatedb || '|' || rolcreaterole || '|' || rolreplication || '|' || "
        "rolbypassrls || '|' || rolinherit FROM pg_roles WHERE rolname = 'nl_media_scanner'"
    )
    assert attrs == "false|false|false|false|false|false"
    assert (
        db.sql(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = 'nl_media_scanner'"
        )
        == "0"
    )
    # Inventaire EXACT des privilèges de table : rien d'autre que le strict nécessaire.
    grants = db.sql(
        "SELECT string_agg(table_name || ':' || privilege_type, ',' ORDER BY table_name, privilege_type) "
        "FROM information_schema.role_table_grants WHERE grantee = 'nl_media_scanner'"
    )
    assert grants == "media_assets:SELECT,media_scan_events:SELECT,role_permissions:SELECT"
    cols = db.sql(
        "SELECT string_agg(table_name || '.' || column_name || ':' || privilege_type, ',' ORDER BY 1) FROM ("
        " SELECT table_name, column_name, privilege_type FROM information_schema.column_privileges "
        " WHERE grantee = 'nl_media_scanner' AND table_name IN ('users', 'user_roles') "
        " OR (grantee = 'nl_media_scanner' AND privilege_type IN ('UPDATE', 'INSERT'))) s"
    )
    for expected in (
        "media_assets.state:UPDATE",
        "media_assets.scan_attempt:UPDATE",
        "media_assets.clean_key:UPDATE",
        "users.id:SELECT",
        "users.status:SELECT",
        "users.deleted_at:SELECT",
        "media_scan_events.sha256:INSERT",
    ):
        assert expected in cols
    for forbidden in (
        "users.email",
        "users.password_hash",
        "media_assets.purged_at:UPDATE",
        "media_assets.deleted_at:UPDATE",
        "media_assets.sha256:UPDATE",
        "media_assets.display_name:UPDATE",
        "media_assets.legal_hold:UPDATE",
    ):
        assert forbidden not in cols
    for table in (
        "audit_logs",
        "lesson_versions",
        "programs",
        "auth_sessions",
        "invitations",
        "media_usage",
        "lesson_version_media",
    ):
        assert db.sql(f"SELECT has_table_privilege('nl_media_scanner', '{table}', 'SELECT')") == "f", table


# =============================================================================================
# 2. Cycle d'analyse par la session de service ; aucune autre session ne peut produire un verdict
# =============================================================================================
def test_full_cycle_only_through_the_service_session(m, w, db, sc):
    u = w.user("instructor")
    mid = m.received(u)
    assert sc.sd.sql("SELECT nl_media_scanner_identity()") == ACCOUNT
    sc.start(mid)
    sc.run(scan_event_sql(mid, 1))
    sc.run(to_clean(mid))
    assert m.state(mid) == "clean"
    assert db.sql(f"SELECT DISTINCT scanned_by FROM media_scan_events WHERE media_id = '{mid}'") == ACCOUNT


def test_other_sessions_cannot_scan_even_declaring_the_service_actor(m, w, db, sc):
    """Session de l'API (superutilisateur de développement) ou d'un client : un acteur déclaré ne suffit pas."""
    u, admin = w.user("instructor"), w.user("admin")
    mid = m.received(u)
    for actor in (ACCOUNT, admin, u, None):
        assert "nl_media_scanner" in m.fails(
            actor, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'"
        )
    sc.start(mid)
    for actor in (ACCOUNT, admin):
        assert "nl_media_scanner" in m.fails(actor, scan_event_sql(mid, 1))
    sc.run(scan_event_sql(mid, 1))
    for actor in (ACCOUNT, admin):
        assert "nl_media_scanner" in m.fails(actor, to_clean(mid))
    assert m.state(mid) == "scanning"
    assert "nl_media_scanner" in db.fails("SELECT nl_media_scanner_identity()")


def test_service_login_cannot_impersonate_nor_touch_other_fields(m, w, db, sc):
    u, other = w.user("instructor"), w.user("admin")
    mid = m.received(u)
    # Connexion de service sous une autre identité : refusée, même pour une transition d'analyse.
    for actor in (u, other, None):
        sc.fails(f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'", actor=actor)
    denied = [
        f"UPDATE media_assets SET display_name = 'x' WHERE id = '{mid}'",
        f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{mid}'",
        f"UPDATE media_assets SET purged_at = now() WHERE id = '{mid}'",
        f"UPDATE media_assets SET sha256 = '{'c' * 64}' WHERE id = '{mid}'",
        f"UPDATE media_assets SET legal_hold = true, legal_hold_reason = 'essai fictif' WHERE id = '{mid}'",
        f"DELETE FROM media_assets WHERE id = '{mid}'",
        "SELECT email FROM users LIMIT 1",
        "SELECT count(*) FROM audit_logs",
        "SELECT count(*) FROM lesson_versions",
        f"INSERT INTO user_roles (user_id, role_id) SELECT '{u}', id FROM roles WHERE code = 'media_scanner'",
        "ALTER TABLE media_assets DISABLE TRIGGER USER",
        "SET session_replication_role = replica",
    ]
    for sql in denied:
        err = sc.fails(sql)
        assert "permission denied" in err or "must be owner" in err or "nl_media_scanner" in err, sql
    assert m.state(mid) == "quarantined"


def test_clean_requires_a_valid_fresh_verdict_on_the_same_bytes(m, w, sc):
    mid = m.received(w.user("instructor"))
    err = sc.fails(to_clean(mid))  # pas d'analyse en cours
    assert "interdite" in err or "nl:immutable" in err
    sc.start(mid)
    assert "traçable" in sc.fails(to_clean(mid))  # aucun verdict
    assert "autre tentative" in sc.fails(scan_event_sql(mid, 2))  # mauvaise tentative
    assert "ck_media_scan_clean_traceable" in sc.fails(scan_event_sql(mid, 1, age="49 hours"))  # signatures périmées
    sc.run(scan_event_sql(mid, 1, sha="d" * 64))  # verdict sur d'autres octets
    assert "empreinte" in sc.fails(to_clean(mid))
    sc.run(scan_event_sql(mid, 1, result="infected"))
    assert "traçable" in sc.fails(to_clean(mid))
    sc.run(f"UPDATE media_assets SET state = 'infected' WHERE id = '{mid}'")
    assert m.state(mid) == "infected"
    # Un média infecté ne devient jamais « clean » sans nouvelle analyse complète.
    err = sc.fails(to_clean(mid))
    assert "interdite" in err or "nl:immutable" in err


def test_append_only_verdicts_and_service_audit(m, w, db, sc):
    mid = m.received(w.user("instructor"))
    sc.start(mid)
    sc.run(scan_event_sql(mid, 1, result="error"))
    for sql in ("UPDATE media_scan_events SET result = 'clean'", "DELETE FROM media_scan_events"):
        sc.fails(sql)
        m.fails(None, sql)
    sc.run(
        f"SELECT nl_media_scanner_audit('media.scan_failed', '{mid}', {q(json.dumps({'attempt': 1, 'reason': 'clamd_unavailable'}))}::jsonb)"
    )
    row = db.sql(
        f"SELECT actor_user_id || '|' || actor_role || '|' || (metadata->>'reason') FROM audit_logs "
        f"WHERE action = 'media.scan_failed' AND entity_id = '{mid}'"
    )
    assert row == f"{ACCOUNT}|media_scanner|clamd_unavailable"
    assert "invalide" in sc.fails(f"SELECT nl_media_scanner_audit('lesson.publish', '{mid}', '{{}}'::jsonb)")
    assert "invalide" in sc.fails(f"SELECT nl_media_scanner_audit('media.scan_x', '{mid}', '[1]'::jsonb)")
    assert "nl_media_scanner" in m.fails(
        ACCOUNT, f"SELECT nl_media_scanner_audit('media.scan_failed', '{mid}', '{{}}'::jsonb)"
    )


# =============================================================================================
# 3. Le rôle métier ne peut être ni étendu, ni attribué, ni utilisé pour se connecter à l'API
# =============================================================================================
def test_business_role_cannot_be_granted_extended_or_used_to_log_in(db, w):
    u = w.user("instructor")
    bypass = "BEGIN; SELECT set_config('nl.role_grant_bypass', 'on', true); {} ; COMMIT;"
    assert "compte de service" in db.fails(
        bypass.format(
            f"INSERT INTO user_roles (user_id, role_id) SELECT '{u}', id FROM roles WHERE code = 'media_scanner'"
        )
    )
    assert "aucun autre rôle" in db.fails(
        bypass.format(
            f"INSERT INTO user_roles (user_id, role_id) SELECT '{ACCOUNT}', id FROM roles WHERE code = 'admin'"
        )
    )
    assert "ne détient que media:scan" in db.fails(
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'content:publish' FROM roles WHERE code = 'media_scanner'"
    )
    assert "invitation" in db.fails(
        "INSERT INTO invitations (email, role_id, token_hash, expires_at, invited_by) SELECT 'x@example.test', id, "
        f"'{'e' * 64}', now() + interval '1 day', '{u}' FROM roles WHERE code = 'media_scanner'"
    )
    assert "Aucune session" in db.fails(
        f"INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at) VALUES ('{ACCOUNT}', '{'f' * 64}', now() + interval '1 day')"
    )
    assert "mot de passe" in db.fails(f"UPDATE users SET password_hash = 'argon2-fictif' WHERE id = '{ACCOUNT}'")
    # media:scan ne peut toujours pas être donnée à un autre rôle (garde 0007 conservée).
    assert "media_scanner" in db.fails(
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'admin'"
    )


# =============================================================================================
# 4. Migration : base vide et base existante ; retour arrière sûr ou refusé
# =============================================================================================
def test_empty_database_up_down_up_is_exact(scratch):
    d = scratch("empty", upto="0007_secure_media_foundation", seeds=False)
    before = _signature(d)
    d.file(UP)
    after = _signature(d)
    assert (
        d.sql("SELECT count(*) FROM users") == "0"
    )  # pas de langues : compte créé plus tard par les données de référence
    d.file(DOWN)
    assert _signature(d) == before
    d.file(UP)
    assert _signature(d) == after
    for seed in sorted((SCHEMA.parent / "seeds").glob("*.sql")):
        d.file(seed)
    assert d.sql(f"SELECT count(*) FROM users WHERE id = '{ACCOUNT}'") == "1"


def test_existing_installation_with_media_then_downgrade_rules(scratch):
    d = scratch("existing", upto="0007_secure_media_foundation")
    w, m = World(d), Media(d)
    u = w.user("instructor")
    mids = [m.received(u) for _ in range(3)]
    pending = m.reserve(u)
    counts = "SELECT (SELECT count(*) FROM media_assets) || '|' || (SELECT count(*) FROM users) || '|' || (SELECT count(*) FROM audit_logs)"
    before, sig7 = d.sql(counts), _signature(d)
    d.file(UP)
    assert {m.state(x) for x in mids} == {"quarantined"} and m.state(pending) == "pending_upload"
    assert d.sql(f"SELECT count(*) FROM users WHERE id = '{ACCOUNT}'") == "1"
    d.file(DOWN)  # aucune analyse encore : retour exact
    assert _signature(d) == sig7 and d.sql(counts) == before
    d.file(UP)
    sc = Scanner(scanner_login(d))
    sc.start(mids[0])
    sc.run(scan_event_sql(mids[0], 1, result="timeout"))
    with pytest.raises(SqlError) as exc:
        d.file(DOWN)
    assert "Retour arrière 0008 refusé" in str(exc.value)
    assert d.sql("SELECT count(*) FROM media_scan_events") == "1"


def test_migration_is_idempotent_on_a_preexisting_login_role(scratch, db):
    """Le rôle de connexion existe déjà (autre base du cluster) : 0008 le réutilise et le désarme."""
    d = scratch("again", upto="0007_secure_media_foundation")
    d.admin("ALTER ROLE nl_media_scanner CREATEDB")
    d.file(UP)
    assert d.sql("SELECT rolcreatedb FROM pg_roles WHERE rolname = 'nl_media_scanner'") == "f"
    assert d.sql("SELECT count(*) FROM roles WHERE code = 'media_scanner'") == "1"

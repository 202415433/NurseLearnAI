"""D14 — séparation des rôles PostgreSQL (migration 0009), sur de VRAIES connexions.

* `nl_owner` : propriétaire de tout le schéma public, NOLOGIN, sans membre.
* `nl_api`   : connexion de l'API, sans privilège d'administration ni propriété.
* `nl_media_scanner` : worker antivirus (0008), inchangé.
Les tests adversariaux s'exécutent CONNECTÉS en `nl_api` (mot de passe d'exploitation fourni par
NL_TEST_API_PASSWORD sur une pile partagée, sinon aléatoire). Données FICTIVES.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess

import pytest
from conftest import SCHEMA, Db, SqlError, build_db, new_id
from editorial import EditorialV2, q
from test_media_foundation import Media
from test_media_scanner_role import ACCOUNT, Scanner, scanner_login, to_clean
from test_program_versions import World, _signature

UP = SCHEMA / "0009_postgres_role_separation.sql"
DOWN = SCHEMA / "0009_postgres_role_separation_down.sql"
M8 = "0008_media_scanner_role"


class ApiDb(Db):
    """Même base, connexion ouverte par le rôle PostgreSQL de l'API (nl_api)."""

    def __init__(self, name: str, password: str):
        super().__init__(name)
        self.base[self.base.index("-U") + 1] = "nl_api"
        self.password = password

    def _run(self, args: list[str], db: str | None = None) -> str:
        env = {**os.environ, "PGPASSWORD": self.password}
        proc = subprocess.run(self.base + ["-d", db or self.name] + args, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise SqlError(proc.stderr.strip())
        return proc.stdout.strip()


def api_login(d: Db) -> ApiDb:
    password = os.environ.get("NL_TEST_API_PASSWORD", "")
    if not password:
        password = secrets.token_urlsafe(24)
        d.admin(f"ALTER ROLE nl_api LOGIN PASSWORD '{password}'")
    return ApiDb(d.name, password)


@pytest.fixture
def api(db) -> ApiDb:
    return api_login(db)


@pytest.fixture
def w(db) -> World:
    return World(db)


@pytest.fixture
def m(db) -> Media:
    return Media(db)


@pytest.fixture
def scratch():
    created: list[Db] = []

    def _make(tag: str, upto: str = "0009_postgres_role_separation", seeds: bool = True) -> Db:
        d = build_db(f"nl_d14_{tag}_{os.getpid()}", upto=upto, seeds=seeds)
        created.append(d)
        return d

    yield _make
    for d in created:
        d.admin(f"DROP DATABASE IF EXISTS {d.name} WITH (FORCE)")


def refused(api: ApiDb, sql: str, actor: str | None = None) -> str:
    return EditorialV2(api).fails(actor, sql) if actor else api.fails(sql)


# =============================================================================================
# 1. Rôles, propriétaires, privilèges (inventaire)
# =============================================================================================
def test_roles_are_unprivileged_and_isolated(db):
    rows = db.sql(
        "SELECT string_agg(rolname || ':' || rolsuper || rolcreatedb || rolcreaterole || rolreplication || rolbypassrls "
        "|| rolinherit, ',' ORDER BY rolname) FROM pg_roles WHERE rolname IN ('nl_owner', 'nl_api', 'nl_media_scanner')"
    )
    assert (
        rows == "nl_api:falsefalsefalsefalsefalsefalse,nl_media_scanner:falsefalsefalsefalsefalsefalse,"
        "nl_owner:falsefalsefalsefalsefalsefalse"
    )
    assert db.sql("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'nl_owner'") == "f"
    # Aucune appartenance : personne n'est membre de nl_owner ; les connexions applicatives de rien.
    assert (
        db.sql(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
            "WHERE g.rolname = 'nl_owner' OR u.rolname IN ('nl_api', 'nl_media_scanner', 'nl_owner')"
        )
        == "0"
    )


def test_every_schema_object_belongs_to_nl_owner(db):
    not_owned = db.sql(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
        "AND c.relowner <> 'nl_owner'::regrole AND NOT EXISTS (SELECT 1 FROM pg_depend d "
        "WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')"
    )
    funcs = db.sql(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
        "AND p.proowner <> 'nl_owner'::regrole AND NOT EXISTS (SELECT 1 FROM pg_depend d "
        "WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')"
    )
    assert (not_owned, funcs) == ("0", "0")
    assert db.sql("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'") == "nl_owner"


def test_security_definer_functions_are_justified_and_locked(db):
    rows = db.sql(
        "SELECT string_agg(p.proname || '|' || pg_get_userbyid(p.proowner) || '|' || "
        "coalesce(array_to_string(p.proconfig, ';'), 'AUCUN') || '|' || "
        "has_function_privilege('public', p.oid, 'EXECUTE') || '|' || has_function_privilege('nl_api', p.oid, 'EXECUTE'), "
        "E'\\n' ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.prosecdef"
    ).splitlines()
    assert rows == [
        # 0010 (2.2.3-e) : rattachement / détachement et conservation légale, par fonctions contrôlées
        # seulement (l'API n'a aucun droit d'écriture sur lesson_version_media ni sur legal_hold).
        "nl_media_attach|nl_owner|search_path=public, pg_temp|false|true",
        "nl_media_detach|nl_owner|search_path=public, pg_temp|false|true",
        "nl_media_reserve|nl_owner|search_path=public, pg_temp|false|true",  # réservation de quota (API)
        "nl_media_scanner_audit|nl_owner|search_path=public, pg_temp|false|false",  # audit du worker
        "nl_media_scanner_identity|nl_owner|search_path=public, pg_temp|false|false",
        "nl_media_scanner_provision|nl_owner|search_path=public, pg_temp|false|false",
        "nl_media_set_legal_hold|nl_owner|search_path=public, pg_temp|false|true",
        "nl_media_usage_row|nl_owner|search_path=public, pg_temp|false|false",  # interne à la réservation
    ]


def test_api_table_privileges_are_minimal(db):
    def has(table: str, priv: str) -> bool:
        return db.sql(f"SELECT has_table_privilege('nl_api', '{table}', '{priv}')") == "t"

    tables = db.sql("SELECT string_agg(tablename, ' ') FROM pg_tables WHERE schemaname = 'public'").split()
    for t in tables:  # jamais TRUNCATE, REFERENCES ni TRIGGER, sur aucune table
        assert not any(has(t, p) for p in ("TRUNCATE", "REFERENCES", "TRIGGER")), t
    for t in (
        "roles",
        "permissions",
        "role_permissions",
        "media_usage",
        "media_scan_events",
        "locales",
        "jurisdictions",
        "professions",
        "specialties",
        "lesson_version_media",
        "payments",
        "subscriptions",
    ):
        assert not any(has(t, p) for p in ("INSERT", "UPDATE", "DELETE")), t
    for t in ("audit_logs", "editorial_decisions"):
        assert has(t, "INSERT") and not has(t, "UPDATE") and not has(t, "DELETE"), t
    for col in (
        "scan_attempt",
        "clean_key",
        "clean_at",
        "access_epoch",
        "purged_at",
        "legal_hold",
        "legal_hold_reason",
    ):
        assert db.sql(f"SELECT has_column_privilege('nl_api', 'media_assets', '{col}', 'UPDATE')") == "f", col
    assert db.sql("SELECT has_schema_privilege('nl_api', 'public', 'CREATE')") == "f"
    assert db.sql("SELECT has_database_privilege('nl_api', current_database(), 'TEMPORARY')") == "f"
    assert db.sql("SELECT has_database_privilege('nl_api', current_database(), 'CREATE')") == "f"


# =============================================================================================
# 2. Douze opérations adversariales refusées à l'API (connexion nl_api réelle)
# =============================================================================================
def test_api_cannot_disable_triggers_or_alter_functions(api):
    assert "owner" in refused(api, "ALTER TABLE media_assets DISABLE TRIGGER USER")  # 1
    assert "owner" in refused(api, "ALTER TABLE audit_logs DISABLE TRIGGER ALL")
    assert "permission denied" in refused(api, "SET session_replication_role = replica")
    assert "permission denied" in refused(
        api, "CREATE OR REPLACE FUNCTION nl_actor() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'"
    )  # 2
    assert "owner" in refused(api, "ALTER FUNCTION nl_media_reserve(text, uuid, uuid, bigint) SECURITY INVOKER")
    assert "owner" in refused(api, "DROP FUNCTION nl_forbid_modification() CASCADE")


def test_api_cannot_touch_verdicts_or_force_clean(m, w, db, api):
    u = w.user("instructor")
    mid = m.received(u)
    sc = Scanner(scanner_login(db))
    sc.start(mid)
    sc.run(f"""INSERT INTO media_scan_events (media_id, attempt, result, engine, sha256, scanned_by)
               VALUES ('{mid}', 1, 'timeout', 'clamav', (SELECT sha256 FROM media_assets WHERE id = '{mid}'), '{ACCOUNT}')""")
    assert "permission denied" in refused(api, "UPDATE media_scan_events SET result = 'clean'")  # 3
    assert "permission denied" in refused(api, "DELETE FROM media_scan_events")
    assert "permission denied" in refused(
        api,
        f"INSERT INTO media_scan_events (media_id, attempt, result, engine, sha256, scanned_by) "
        f"SELECT id, 1, 'clean', 'clamav', sha256, '{ACCOUNT}' FROM media_assets WHERE id = '{mid}'",
        ACCOUNT,
    )
    assert "permission denied" in refused(api, to_clean(mid), ACCOUNT)  # 4 (clean_key)
    assert "nl_media_scanner" in refused(api, f"UPDATE media_assets SET state = 'clean' WHERE id = '{mid}'", ACCOUNT)
    assert "permission denied" in refused(api, f"UPDATE media_assets SET scan_attempt = 9 WHERE id = '{mid}'", u)
    assert m.state(mid) == "scanning"


def test_api_cannot_change_quotas_or_erase_audit(db, w, api):
    admin = w.user("admin")
    db.sql("SELECT nl_media_usage_row('platform', NULL, NULL)")
    assert "permission denied" in refused(api, "UPDATE media_usage SET quota_bytes = quota_bytes * 10", admin)  # 5
    assert "permission denied" in refused(api, "DELETE FROM media_usage")
    assert "permission denied" in refused(api, "SELECT nl_media_usage_row('user', gen_random_uuid(), NULL)")
    assert "permission denied" in refused(api, "DELETE FROM audit_logs")  # 6
    assert "permission denied" in refused(api, "TRUNCATE audit_logs")
    assert "permission denied" in refused(api, "UPDATE audit_logs SET action = 'x.y'")


def test_api_cannot_grant_itself_media_scan_or_admin(db, w, api):
    u = w.user("instructor")
    assert "permission denied" in refused(
        api,
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'admin'",
    )  # 7
    bypass = (
        "SELECT set_config('nl.role_grant_bypass', 'on', true); INSERT INTO user_roles (user_id, role_id) "
        "SELECT '{u}', id FROM roles WHERE code = '{role}'"
    )
    for role in ("media_scanner", "admin", "medical_reviewer"):
        err = refused(api, bypass.format(u=u, role=role), u)
        assert "nl.role_grant_bypass" in err or "compte de service" in err, role
    assert db.sql(f"SELECT count(*) FROM user_roles WHERE user_id = '{u}'") == "1"


def test_api_cannot_modify_a_frozen_edition(db, w, api):
    ed = EditorialV2(db)
    author, reviewer = w.user("instructor"), w.user("medical_reviewer")
    _, (lesson,) = w.module()
    vid = ed.approved_version(lesson, author, reviewer)
    err = refused(api, f"UPDATE lesson_versions SET title = 'Réécrite' WHERE id = '{vid}'", author)  # 8
    assert "immuable" in err.lower() or "interdit" in err.lower() or "nl:immutable" in err
    assert "permission denied" in refused(api, f"DELETE FROM editorial_decisions WHERE entity_id = '{vid}'")
    forged = (
        "SELECT set_config('nl.editorial_log', 'on', true); INSERT INTO editorial_decisions "
        "(entity_type, entity_id, from_status, to_status, decision, actor_id) VALUES "
        f"('lesson_version', '{vid}', 'medical_review', 'approved', 'approved', '{reviewer}')"
    )
    assert "transitions éditoriales" in refused(api, forged, reviewer)


def test_api_cannot_take_ownership_or_escalate(db, api):
    assert "owner" in refused(api, "ALTER TABLE users OWNER TO nl_api")  # 9
    assert "owner" in refused(api, "ALTER SCHEMA public OWNER TO nl_api")
    # Superutilisateur d'administration RÉEL de ce cluster (postgres en CI, nurselearn sous Docker).
    admin = db.sql("SELECT current_user")
    for role in ("nl_owner", "nl_media_scanner", admin):  # 10
        assert "permission denied" in refused(api, f"SET ROLE {role}"), role
    assert "permission denied" in refused(api, "SET SESSION AUTHORIZATION nl_owner")
    assert "permission denied" in refused(api, "CREATE TABLE intrus (a int)")  # 11
    assert "permission denied" in refused(api, "CREATE TEMP TABLE intrus (a int)")
    assert "permission denied" in refused(api, "CREATE FUNCTION pg_temp.f() RETURNS int LANGUAGE sql AS 'SELECT 1'")
    assert "permission denied" in refused(api, "CREATE SCHEMA intrus")


def test_api_cannot_bypass_constraints_through_functions_or_triggers(api):
    err = refused(  # 12
        api, "CREATE TRIGGER t BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at()"
    )
    assert "permission denied" in err or "owner" in err
    for fn in (
        "nl_enforce_ownership()",
        "nl_media_scanner_provision()",
        "nl_media_scanner_identity()",
        "nl_media_scanner_audit('media.scan_x', NULL, '{}')",
    ):
        assert "permission denied" in refused(api, f"SELECT {fn}"), fn
    assert "permission denied" in refused(api, "COPY (SELECT 1) TO PROGRAM 'id'")
    assert "permission denied" in refused(api, "SELECT pg_read_file('/etc/hostname')")
    assert "permission denied" in refused(api, "DO $$ BEGIN PERFORM 1; END $$; CREATE TABLE x (a int)")


# =============================================================================================
# 3. Opérations légitimes de l'API et du worker, sous leurs connexions réelles
# =============================================================================================
def test_legitimate_api_operations_still_work(db, w, m, api):
    ed_api = EditorialV2(api)
    author = w.user("instructor")
    _, (lesson,) = w.module()
    vid = EditorialV2(db).lesson_version(lesson, author)
    EditorialV2(db).cite_source(vid)
    ed_api.submit(vid, author)  # transition : décision écrite par le déclencheur (profondeur 2)
    assert db.sql(f"SELECT decision FROM editorial_decisions WHERE entity_id = '{vid}'") == "submitted"
    mid = Media(api).reserve(author)  # réservation : quota via la fonction du propriétaire
    assert m.state(mid) == "pending_upload"
    ed_api.run(
        author, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'upload_aborted' WHERE id = '{mid}'"
    )
    api.sql(
        f"INSERT INTO audit_logs (action, entity_type, entity_id, metadata) VALUES "
        f"('test.d_fourteen', 'test', '{new_id()}', {q(json.dumps({'ok': True}))}::jsonb)"
    )
    uid = new_id()
    api.sql(
        f"INSERT INTO users (id, email, status, email_verified_at) VALUES ('{uid}', '{uid[:8]}@example.test', 'active', now())"
    )
    api.sql(f"UPDATE users SET locale = 'en' WHERE id = '{uid}'")


def test_worker_still_scans_after_d14(db, w, m):
    mid = m.received(w.user("instructor"))
    sc = Scanner(scanner_login(db))
    sc.start(mid)
    sc.run(f"""INSERT INTO media_scan_events (media_id, attempt, result, engine, engine_version, signature_version,
               signatures_published_at, sha256, scanned_by) SELECT id, 1, 'clean', 'clamav', '1.5.5', '28191',
               now() - interval '1 hour', sha256, '{ACCOUNT}' FROM media_assets WHERE id = '{mid}'""")
    sc.run(to_clean(mid))
    assert m.state(mid) == "clean"
    assert "permission denied" in sc.sd.fails("SELECT count(*) FROM audit_logs")


# =============================================================================================
# 4. Installations neuves et existantes ; retour arrière exact
# =============================================================================================
def test_new_installation_up_down_up_is_exact(scratch):
    d = scratch("empty", upto=M8, seeds=False)
    before = _signature(d)
    d.file(UP)
    after = _signature(d)
    d.file(DOWN)
    assert _signature(d) == before
    assert d.sql("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'") == "pg_database_owner"
    d.file(UP)
    assert _signature(d) == after


def test_existing_installation_keeps_all_data(scratch):
    d = scratch("existing", upto=M8)
    w, m = World(d), Media(d)
    u = w.user("instructor")
    mids = [m.received(u) for _ in range(2)]
    counts = (
        "SELECT (SELECT count(*) FROM users) || '|' || (SELECT count(*) FROM media_assets) || '|' || "
        "(SELECT count(*) FROM audit_logs) || '|' || (SELECT count(*) FROM user_roles)"
    )
    before = d.sql(counts)
    d.file(UP)
    assert d.sql(counts) == before and {m.state(x) for x in mids} == {"quarantined"}
    a = api_login(d)
    assert a.sql(f"SELECT count(*) FROM media_assets WHERE id IN ('{mids[0]}', '{mids[1]}')") == "2"
    d.file(DOWN)
    assert d.sql(counts) == before

"""Migration 0010 — rattachement contrôlé des médias (D3-3) et conservation légale (2.2.3-e, E1).

Tests ADVERSARIAUX sur PostgreSQL réel, avec la VRAIE connexion de l'API (`nl_api`) : l'API n'a
aucun droit d'écriture sur `lesson_version_media`, sur les colonnes de conservation ni sur le journal
des conservations ; elle passe par trois fonctions SECURITY DEFINER qui exigent un acteur, ses droits
dans la portée, un brouillon, un média de la bonne portée et dans un état admissible.
Comptes et contenus FICTIFS (`@example.test`).
"""

from __future__ import annotations

import pytest
from conftest import SCHEMA, build_db, new_id
from editorial import EditorialV2
from test_media_foundation import Media, scan_event_sql
from test_media_scanner_role import Scanner, scanner_login, to_clean
from test_postgres_roles import ApiDb, api_login
from test_program_versions import World

UP = SCHEMA / "0010_media_editorial_permissions.sql"
DOWN = SCHEMA / "0010_media_editorial_permissions_down.sql"
REASON = "Enquête administrative fictive (test)"


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
def sc(db) -> Scanner:
    return Scanner(scanner_login(db))


def as_api(api: ApiDb, actor: str | None, sql: str) -> str:
    out = EditorialV2(api).run(actor, sql)
    return out.splitlines()[-1] if out else out  # dernière ligne : résultat (la première est l'acteur)


def refused(api: ApiDb, actor: str | None, sql: str) -> str:
    return EditorialV2(api).fails(actor, sql) if actor else api.fails(sql)


def clean(m: Media, sc: Scanner, uploader: str, **kw) -> str:
    mid = m.received(uploader, **kw)
    sc.start(mid)
    sc.run(scan_event_sql(mid, 1))
    sc.run(to_clean(mid))
    return mid


def draft(db, w: World, author: str, org: str | None = None) -> str:
    _, lessons = w.module(org)
    return EditorialV2(db).lesson_version(lessons[0], author)


def attach(lv: str, mid: str) -> str:
    return f"SELECT nl_media_attach('{lv}', '{mid}')"


def detach(lv: str, mid: str) -> str:
    return f"SELECT nl_media_detach('{lv}', '{mid}')"


# =============================================================================================
# 1. Privilèges : aucune écriture directe de l'API
# =============================================================================================
def test_api_has_no_direct_write_on_attachments_hold_or_journal(db, api, w, m):
    for table, priv in (
        ("lesson_version_media", "INSERT"),
        ("lesson_version_media", "DELETE"),
        ("lesson_version_media", "UPDATE"),
        ("media_legal_hold_events", "INSERT"),
        ("media_legal_hold_events", "UPDATE"),
        ("media_legal_hold_events", "DELETE"),
        ("media_legal_hold_events", "TRUNCATE"),
    ):
        assert db.sql(f"SELECT has_table_privilege('nl_api', '{table}', '{priv}')") == "f", (table, priv)
    for col in ("legal_hold", "legal_hold_reason"):
        assert db.sql(f"SELECT has_column_privilege('nl_api', 'media_assets', '{col}', 'UPDATE')") == "f"
    assert db.sql("SELECT has_table_privilege('nl_api', 'media_legal_hold_events', 'SELECT')") == "t"
    for fn in (
        "nl_media_attach(uuid,uuid)",
        "nl_media_detach(uuid,uuid)",
        "nl_media_set_legal_hold(uuid,boolean,text)",
    ):
        assert db.sql(f"SELECT has_function_privilege('nl_api', '{fn}', 'EXECUTE')") == "t"
        assert db.sql(f"SELECT has_function_privilege('nl_media_scanner', '{fn}', 'EXECUTE')") == "f"
        assert db.sql(f"SELECT has_function_privilege('public', '{fn}', 'EXECUTE')") == "f"

    author = w.user("instructor")
    admin = w.user("admin")
    lv = draft(db, w, author)
    mid = m.received(author)
    for sql in (
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv}', '{mid}', '{author}')",
        f"UPDATE media_assets SET legal_hold = true, legal_hold_reason = 'Contournement' WHERE id = '{mid}'",
        f"INSERT INTO media_legal_hold_events (media_id, action, reason, actor_user_id) VALUES ('{mid}', 'set', 'Contournement', '{admin}')",
    ):
        assert "permission denied" in refused(api, admin, sql)


# =============================================================================================
# 2. Rattachement / détachement : chemin nominal et idempotence
# =============================================================================================
def test_attach_and_detach_through_the_controlled_functions(db, api, w, m, sc):
    author = w.user("instructor")
    lv = draft(db, w, author)
    received = m.received(author)  # en quarantaine : rattachable à un brouillon
    ok = clean(m, sc, author)
    assert as_api(api, author, attach(lv, received)) == "t"
    assert as_api(api, author, attach(lv, received)) == "f"  # idempotent
    assert as_api(api, author, attach(lv, ok)) == "t"
    assert db.sql(f"SELECT count(*) FROM lesson_version_media WHERE lesson_version_id = '{lv}'") == "2"
    assert db.sql(f"SELECT attached_by FROM lesson_version_media WHERE media_id = '{ok}'") == author
    assert as_api(api, author, detach(lv, received)) == "t"
    assert as_api(api, author, detach(lv, received)) == "f"
    assert db.sql(f"SELECT count(*) FROM lesson_version_media WHERE lesson_version_id = '{lv}'") == "1"


# =============================================================================================
# 3. Refus : acteur, droits, établissements, état du média, éditions figées
# =============================================================================================
def test_attach_requires_an_actor_and_editorial_rights(db, api, w, m):
    author = w.user("instructor")
    lv = draft(db, w, author)
    mid = m.received(author)
    assert "acteur" in refused(api, None, "BEGIN; " + attach(lv, mid) + "; COMMIT;")
    for intruder in (w.student(), w.user("medical_reviewer"), w.user("org_admin", org=w.org())):
        msg = refused(api, intruder, attach(lv, mid))
        assert "content:create" in msg or "nl:forbidden" in msg or "refusé" in msg, msg
    # Un formateur d'établissement n'agit pas sur une leçon publique.
    org = w.org()
    org_author = w.user("instructor", org=org)
    assert "refusé" in refused(api, org_author, attach(lv, mid))


def test_establishment_isolation(db, api, w, m):
    org_a, org_b = w.org(), w.org()
    author_a = w.user("instructor", org=org_a)
    platform = w.user("instructor")
    lv_a = draft(db, w, author_a, org=org_a)
    lv_public = draft(db, w, platform)
    media_b = m.received(w.user("instructor", org=org_b), org=org_b)
    media_a = m.received(author_a, org=org_a)
    # Média d'un autre établissement : refusé, même par un auteur habilité dans sa portée.
    assert "autre établissement" in refused(api, author_a, attach(lv_a, media_b))
    # Média d'établissement sur une leçon publique : refusé.
    assert "autre établissement" in refused(api, platform, attach(lv_public, media_a))
    # Média public sur une leçon d'établissement : admis (contenu commun).
    public_media = m.received(platform)
    assert as_api(api, author_a, attach(lv_a, public_media)) == "t"
    assert as_api(api, author_a, attach(lv_a, media_a)) == "t"


def test_media_state_and_revocation_are_enforced(db, api, w, m, sc):
    author = w.user("instructor")
    reviewer = w.user("medical_reviewer")
    lv = draft(db, w, author)
    pending = m.reserve(author)
    assert "non rattachable" in refused(api, author, attach(lv, pending))
    revoked = clean(m, sc, author)
    as_api(
        api,
        reviewer,
        f"UPDATE media_assets SET state = 'revoked', revoked_reason = 'Retrait fictif' WHERE id = '{revoked}'",
    )
    assert "non rattachable" in refused(api, author, attach(lv, revoked))
    failed = m.received(author)
    sc.start(failed)
    sc.run(scan_event_sql(failed, 1, result="error"))
    sc.run(f"UPDATE media_assets SET state = 'scan_failed' WHERE id = '{failed}'")
    assert "non rattachable" in refused(api, author, attach(lv, failed))
    infected = m.received(author)
    sc.start(infected)
    sc.run(scan_event_sql(infected, 1, result="infected"))
    sc.run(f"UPDATE media_assets SET state = 'infected' WHERE id = '{infected}'")
    assert "non rattachable" in refused(api, author, attach(lv, infected))
    deleted = m.received(author)
    as_api(
        api,
        author,
        f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{deleted}'",
    )
    assert "introuvable" in refused(api, author, attach(lv, deleted))
    assert "introuvable" in refused(api, author, attach(lv, new_id()))
    assert db.sql(f"SELECT count(*) FROM lesson_version_media WHERE lesson_version_id = '{lv}'") == "0"


def test_frozen_versions_are_never_modified(db, api, w, m, sc):
    author = w.user("instructor")
    reviewer = w.user("medical_reviewer")
    e = EditorialV2(db)
    _, lessons = w.module()
    lv = e.lesson_version(lessons[0], author)
    mid = clean(m, sc, author, kind="image")
    as_api(api, author, attach(lv, mid))
    e.verified_source(lv, reviewer)
    e.submit(lv, author)
    other = clean(m, sc, author)
    # Soumise (relecture) puis approuvée : ni rattachement, ni détachement.
    for status_step in ("submitted", "approved"):
        if status_step == "approved":
            e.approve(lv, reviewer)
        assert "brouillon" in refused(api, author, attach(lv, other)) or "protégée" in refused(
            api, author, attach(lv, other)
        )
        assert "protégée" in refused(api, author, detach(lv, mid))
    assert db.sql(f"SELECT count(*) FROM lesson_version_media WHERE lesson_version_id = '{lv}'") == "1"
    # Même la connexion d'administration ne peut pas modifier un rattachement figé.
    assert "protégée" in db.fails(
        f"BEGIN; SELECT set_config('nl.actor_id', '{author}', true); "
        f"DELETE FROM lesson_version_media WHERE lesson_version_id = '{lv}'; COMMIT;"
    )


# =============================================================================================
# 4. Conservation légale contrôlée
# =============================================================================================
def hold(mid: str, on: bool, reason: str | None = REASON) -> str:
    r = "NULL" if reason is None else "'" + reason.replace("'", "''") + "'"
    return f"SELECT nl_media_set_legal_hold('{mid}', {str(on).lower()}, {r}) IS NOT NULL"


def test_legal_hold_requires_the_dedicated_permission_and_a_reason(db, api, w, m):
    author = w.user("instructor")
    admin = w.user("admin")
    mid = m.received(author)
    for intruder in (author, w.user("medical_reviewer"), w.user("org_admin", org=w.org())):
        assert "media:legal_hold" in refused(api, intruder, hold(mid, True))
    for bad in (None, "", "   ", "abc"):
        assert "Justification" in refused(api, admin, hold(mid, True, bad))
    assert as_api(api, admin, hold(mid, True)) == "t"
    assert "Aucun changement" in refused(api, admin, hold(mid, True))
    assert (
        db.sql(f"SELECT legal_hold || '|' || legal_hold_reason FROM media_assets WHERE id = '{mid}'")
        == f"true|{REASON}"
    )
    # Suppression interdite sous conservation (0007), même pour l'auteur.
    assert "conservation" in refused(
        api, author, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{mid}'"
    )
    # Levée : justification obligatoire aussi ; motif et acteur conservés au journal.
    assert "Justification" in refused(api, admin, hold(mid, False, None))
    assert as_api(api, admin, hold(mid, False, "Fin de l'enquête fictive")) == "t"
    rows = db.sql(
        f"SELECT string_agg(action || ':' || actor_user_id || ':' || (created_at IS NOT NULL), ',' ORDER BY created_at, action) "
        f"FROM media_legal_hold_events WHERE media_id = '{mid}'"
    )
    assert rows == f"set:{admin}:true,released:{admin}:true"
    assert db.sql(f"SELECT reason FROM media_legal_hold_events WHERE media_id = '{mid}' AND action = 'released'") == (
        "Fin de l'enquête fictive"
    )
    # Audit (0007) : pose et levée, acteur identifié, sans motif (aucun texte libre dans l'audit).
    audit = db.sql(
        f"SELECT string_agg(action || ':' || actor_user_id, ',' ORDER BY action) FROM audit_logs "
        f"WHERE entity_id = '{mid}' AND action LIKE 'media.legal_hold_%'"
    )
    assert audit == f"media.legal_hold_released:{admin},media.legal_hold_set:{admin}"
    assert REASON not in db.sql(
        f"SELECT coalesce(string_agg(metadata::text, ''), '') FROM audit_logs WHERE entity_id = '{mid}'"
    )


def test_legal_hold_journal_is_append_only_even_for_the_administration(db, api, w, m):
    admin = w.user("admin")
    mid = m.received(w.user("instructor"))
    as_api(api, admin, hold(mid, True))
    for sql in (
        f"UPDATE media_legal_hold_events SET reason = 'Réécrit' WHERE media_id = '{mid}'",
        f"DELETE FROM media_legal_hold_events WHERE media_id = '{mid}'",
        "TRUNCATE media_legal_hold_events",
    ):
        assert db.fails(sql)  # même la connexion d'administration (superutilisateur)
    assert db.sql(f"SELECT count(*) FROM media_legal_hold_events WHERE media_id = '{mid}'") == "1"


def test_legal_hold_permission_is_reserved_to_the_admin_role(db, w):
    assert (
        db.sql(
            "SELECT string_agg(r.code, ',') FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "WHERE rp.permission_code = 'media:legal_hold'"
        )
        == "admin"
    )
    for role in ("org_admin", "medical_reviewer", "instructor", "media_scanner"):
        msg = db.fails(
            f"INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:legal_hold' FROM roles WHERE code = '{role}'"
        )
        assert "nl:forbidden" in msg  # garde 0010 (ou garde 0008 pour le rôle de service)


def test_legal_hold_never_makes_a_media_servable(db, api, w, m, sc):
    """La conservation ne change ni l'état ni le verdict : un média infecté reste infecté."""
    admin = w.user("admin")
    author = w.user("instructor")
    infected = m.received(author)
    sc.start(infected)
    sc.run(scan_event_sql(infected, 1, result="infected"))
    sc.run(f"UPDATE media_assets SET state = 'infected' WHERE id = '{infected}'")
    as_api(api, admin, hold(infected, True))
    assert db.sql(f"SELECT state || '|' || legal_hold FROM media_assets WHERE id = '{infected}'") == "infected|true"
    lv = draft(db, w, author)
    assert "non rattachable" in refused(api, author, attach(lv, infected))


# =============================================================================================
# 5. Migration : réversible, refusée si l'historique légal existe
# =============================================================================================
def test_up_down_up_and_journal_protection():
    d = build_db(f"nl_m10_{new_id()[:8]}")
    try:
        d.file(DOWN)
        assert d.sql("SELECT to_regclass('public.media_legal_hold_events') IS NULL") == "t"
        assert d.sql("SELECT count(*) FROM permissions WHERE code = 'media:legal_hold'") == "0"
        d.file(UP)
        assert d.sql("SELECT count(*) FROM role_permissions WHERE permission_code = 'media:legal_hold'") == "1"
        w = World(d)
        admin = w.user("admin")
        mid = Media(d).received(w.user("instructor"))
        EditorialV2(d).run(admin, hold(mid, True))
        assert "journal des conservations" in d.fails(DOWN.read_text())
    finally:
        d.admin(f"DROP DATABASE IF EXISTS {d.name} WITH (FORCE)")

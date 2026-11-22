"""Migration 0007 — fondations des médias protégés (étape 2.2.3-a), sur PostgreSQL réel.

Comptes et contenus FICTIFS. Aucun point d'accès, aucun worker : seuls le schéma et ses invariants
sont testés. Le passage à `clean` n'est démontré QUE dans une base jetable où un rôle de test
détient `media:scan` ; dans le schéma livré, aucun rôle ne la détient (aucun média diffusable).
"""

from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import MIGRATIONS, SCHEMA, Db, build_db, new_id
from editorial import EditorialV2, q
from test_program_versions import World, _signature

UP = SCHEMA / "0007_secure_media_foundation.sql"
DOWN = SCHEMA / "0007_secure_media_foundation_down.sql"
MIB = 1048576
GIB = 1073741824
SHA = "a" * 64


class Media:
    """Opérations sur les médias, chacune dans une transaction avec un acteur déclaré."""

    def __init__(self, db: Db):
        self.db = db
        self.ed = EditorialV2(db)

    def run(self, actor: str | None, sql: str) -> str:
        return self.ed.run(actor, sql)

    def fails(self, actor: str | None, sql: str) -> str:
        return self.ed.fails(actor, sql)

    def reserve_sql(
        self,
        mid: str,
        uploader: str,
        org: str | None = None,
        kind: str = "image",
        size: int = 1000,
        ttl: str = "1 hour",
        captions_for: str | None = None,
        state: str = "pending_upload",
    ) -> str:
        mime = {"image": "image/png", "video": "video/mp4", "captions": "text/vtt", "document": "application/pdf"}[kind]
        alt = q('{"fr":"Illustration fictive"}') if kind == "image" else "NULL"
        cap = f"'{captions_for}'" if captions_for else "NULL"
        org_sql = f"'{org}'" if org else "NULL"
        return (
            "INSERT INTO media_assets (id, organization_id, uploaded_by, kind, mime_type, declared_bytes, display_name, "
            "license, alt_text_i18n, captions_for, reservation_expires_at, state) VALUES "
            f"('{mid}', {org_sql}, '{uploader}', '{kind}', '{mime}', {size}, 'fichier-test', 'CC-BY-4.0', {alt}, "
            f"{cap}, now() + interval '{ttl}', '{state}')"
        )

    def reserve(self, uploader: str, **kw) -> str:
        mid = new_id()
        self.run(uploader, self.reserve_sql(mid, uploader, **kw))
        return mid

    def receive_sql(self, mid: str, size: int = 900, sha: str = SHA) -> str:
        return (
            f"UPDATE media_assets SET state = 'quarantined', byte_size = {size}, sha256 = '{sha}', "
            f"quarantine_key = '{new_id()}/{new_id()}' WHERE id = '{mid}'"
        )

    def received(self, uploader: str, **kw) -> str:
        size = kw.pop("received", 900)
        mid = self.reserve(uploader, **kw)
        self.run(uploader, self.receive_sql(mid, size))
        return mid

    def usage(self, scope: str, ident: str | None = None) -> tuple[int, int, int]:
        """(occupé, réservé, quota) en octets, lus dans la vue dérivée media_usage_totals."""
        col = {"user": "user_id", "organization": "organization_id"}.get(scope)
        where = f"scope_kind = '{scope}'" + (f" AND {col} = '{ident}'" if col else "")
        row = self.db.sql(
            f"SELECT used_bytes || '|' || reserved_bytes || '|' || quota_bytes FROM media_usage_totals WHERE {where}"
        )
        if not row:
            return (0, 0, 0)
        used, reserved, quota = (int(x) for x in row.split("|"))
        return used, reserved, quota

    def state(self, mid: str) -> str:
        return self.db.sql(f"SELECT state FROM media_assets WHERE id = '{mid}'")

    def epoch(self, mid: str) -> int:
        return int(self.db.sql(f"SELECT access_epoch FROM media_assets WHERE id = '{mid}'"))


@pytest.fixture
def w(db) -> World:
    return World(db)


@pytest.fixture
def m(db) -> Media:
    return Media(db)


@pytest.fixture
def scratch():
    created: list[Db] = []

    def _make(tag: str, upto: str = MIGRATIONS[-1], seeds: bool = True) -> Db:
        d = build_db(f"nl_m7_{tag}_{os.getpid()}", upto=upto, seeds=seeds)
        created.append(d)
        return d

    yield _make
    for d in created:
        d.admin(f"DROP DATABASE IF EXISTS {d.name} WITH (FORCE)")


def grant_test_scanner(d: Db, w: World) -> str:
    """BASE JETABLE UNIQUEMENT : rôle `media_scanner` de test (préfigure l'identité de service 2.2.3-c)."""
    d.sql(
        "INSERT INTO roles (id, code, name_i18n) SELECT coalesce(max(id), 0) + 1, 'media_scanner', "
        '\'{"fr":"Analyseur de test"}\' FROM roles'
    )
    d.sql(
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'media_scanner'"
    )
    return w.user("media_scanner")


def scan_event_sql(mid: str, attempt: int, result: str = "clean", sha: str = SHA, age: str = "1 hour") -> str:
    threat = "'Eicar-Test-Signature'" if result == "infected" else "NULL"
    return (
        "INSERT INTO media_scan_events (media_id, attempt, result, engine, engine_version, signature_version, "
        "signatures_published_at, threat_name, sha256, duration_ms, scanned_by) VALUES "
        f"('{mid}', {attempt}, '{result}', 'clamav', '1.4.2', '27470', now() - interval '{age}', {threat}, "
        f"'{sha}', 120, '{new_id()}')"
    )


# =============================================================================================
# 1–4. Tables, clés étrangères, contraintes, déclencheurs
# =============================================================================================
def test_tables_foreign_keys_and_no_scanner_role(db):
    tables = db.sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_schema = "
        "'public' AND table_name IN ('media_assets','media_scan_events','lesson_version_media','media_usage','learning_resources')"
    )
    assert tables == "learning_resources,lesson_version_media,media_assets,media_scan_events,media_usage"
    assert (
        db.sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
        == "75"
    )
    fks = db.sql(
        "SELECT string_agg(conrelid::regclass::text || '>' || confrelid::regclass::text || ':' || confdeltype::text, ',' ORDER BY 1) "
        "FROM pg_constraint WHERE contype = 'f' AND conrelid::regclass::text IN "
        "('media_assets','media_scan_events','lesson_version_media','media_usage')"
    )
    # Aucune suppression en cascade vers un historique : seuls les rattachements d'un brouillon suivent
    # la suppression (autorisée uniquement en brouillon) de leur version de leçon.
    assert "lesson_version_media>lesson_versions:c" in fks
    assert fks.count(":c") == 1 and ":a" not in fks and ":n" not in fks
    assert db.sql("SELECT count(*) FROM permissions WHERE code = 'media:scan'") == "1"
    assert db.sql("SELECT count(*) FROM role_permissions WHERE permission_code = 'media:scan'") == "0"
    # Ni l'administrateur ni aucun autre rôle ne peut recevoir media:scan.
    err = db.fails(
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'admin'"
    )
    assert "media_scanner" in err
    assert (
        db.sql(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text IN "
            "('media_assets','media_scan_events','lesson_version_media','media_usage')"
        )
        == "9"
    )


def test_constraints_on_formats_sizes_and_accessibility(m, w, db):
    u = w.user("instructor")
    over = {"image": 5 * MIB + 1, "document": 20 * MIB + 1, "captions": 1 * MIB + 1, "video": 200 * MIB + 1}
    for kind, size in over.items():
        video = m.received(u, kind="video", size=1000) if kind == "captions" else None
        err = m.fails(u, m.reserve_sql(new_id(), u, kind=kind, size=size, captions_for=video))
        assert "ck_media_assets_max_size" in err, kind
        m.reserve(u, kind=kind, size=size - 1, captions_for=video)  # taille maximale exacte acceptée
    assert "ck_media_assets_mime" in m.fails(u, m.reserve_sql(new_id(), u).replace("'image/png'", "'image/svg+xml'"))
    assert "ck_media_assets_mime" in m.fails(
        u, m.reserve_sql(new_id(), u, kind="document").replace("'application/pdf'", "'text/html'")
    )
    assert "ck_media_assets_alt" in m.fails(
        u, m.reserve_sql(new_id(), u).replace(q('{"fr":"Illustration fictive"}'), "NULL")
    )
    assert "ck_media_assets_captions" in m.fails(u, m.reserve_sql(new_id(), u, kind="captions"))
    assert "display_name" in m.fails(u, m.reserve_sql(new_id(), u).replace("'fichier-test'", "'../etc/passwd'"))
    admin = w.user("admin")  # permission globale : seule la clé étrangère peut refuser
    assert "foreign key" in m.fails(admin, m.reserve_sql(new_id(), admin, org=new_id())).lower()


def test_creation_requires_session_actor_and_permission(m, w, db):
    instructor, student = w.user("instructor"), w.user("student")
    mid = new_id()
    assert "acteur" in m.fails(None, m.reserve_sql(mid, instructor))  # aucune identité déclarée
    assert "déposant" in m.fails(student, m.reserve_sql(mid, instructor))
    assert "content:create" in m.fails(student, m.reserve_sql(mid, student))
    assert "naît en attente" in m.fails(instructor, m.reserve_sql(mid, instructor, state="clean"))
    assert "Échéance" in m.fails(instructor, m.reserve_sql(mid, instructor, ttl="25 hours"))
    assert "Échéance" in m.fails(instructor, m.reserve_sql(mid, instructor, ttl="-1 second"))
    m.run(instructor, m.reserve_sql(mid, instructor))
    assert m.state(mid) == "pending_upload"


# =============================================================================================
# 5–6. Transitions et tentatives de forçage de « clean »
# =============================================================================================
def test_transitions_allowed_and_forbidden(m, w, db):
    u, other = w.user("instructor"), w.user("instructor")
    reviewer, admin = w.user("medical_reviewer"), w.user("admin")
    mid = m.reserve(u)
    assert "déposant" in m.fails(other, m.receive_sql(mid))  # réception par un autre utilisateur
    for target in ("scanning", "clean", "infected", "scan_failed", "revoked"):  # avant réception : rien
        m.fails(admin, f"UPDATE media_assets SET state = '{target}' WHERE id = '{mid}'")
    m.run(u, m.receive_sql(mid))
    assert m.state(mid) == "quarantined"
    assert "immuable" in m.fails(u, f"UPDATE media_assets SET sha256 = '{'b' * 64}' WHERE id = '{mid}'")
    assert "immuables" in m.fails(admin, f"UPDATE media_assets SET organization_id = '{w.org()}' WHERE id = '{mid}'")
    # Analyse réservée au service antivirus : ni l'auteur, ni un relecteur, ni l'administrateur.
    for actor in (u, reviewer, admin):
        err = m.fails(actor, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'")
        assert "media:scan" in err
    assert "interdite" in m.fails(admin, f"UPDATE media_assets SET state = 'pending_upload' WHERE id = '{mid}'")
    # Révocation : relecteur plateforme ou administrateur ; époque d'accès incrémentée.
    assert "Révocation réservée" in m.fails(
        u, f"UPDATE media_assets SET state = 'revoked', revoked_reason = 'Essai interdit' WHERE id = '{mid}'"
    )
    before = m.epoch(mid)
    m.run(
        reviewer,
        f"UPDATE media_assets SET state = 'revoked', revoked_reason = 'Contenu inapproprié (test)' WHERE id = '{mid}'",
    )
    assert m.state(mid) == "revoked" and m.epoch(mid) == before + 1
    assert "interdite" in m.fails(admin, f"UPDATE media_assets SET state = 'quarantined' WHERE id = '{mid}'")
    assert "décroissante" in m.fails(reviewer, f"UPDATE media_assets SET access_epoch = 0 WHERE id = '{mid}'")


def test_clean_cannot_be_forced_in_the_shipped_schema(m, w, db):
    u, admin = w.user("instructor"), w.user("admin")
    mid = m.received(u)
    forced = [
        f"UPDATE media_assets SET state = 'clean', clean_key = '{new_id()}/{new_id()}', clean_at = now() WHERE id = '{mid}'",
        f"UPDATE media_assets SET clean_key = '{new_id()}/{new_id()}' WHERE id = '{mid}'",
        f"UPDATE media_assets SET clean_at = now() WHERE id = '{mid}'",
        scan_event_sql(mid, 1),
        f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'",
    ]
    for actor in (u, admin, None):
        for sql in forced:
            m.fails(actor, sql)
    assert m.state(mid) == "quarantined"
    assert db.sql("SELECT count(*) FROM media_assets WHERE state = 'clean'") == "0"
    assert db.sql("SELECT count(*) FROM media_scan_events") == "0"


def test_clean_only_from_a_traceable_fresh_verdict(scratch):
    d = scratch("scan")
    w, m = World(d), Media(d)
    scanner = grant_test_scanner(d, w)
    u, admin = w.user("instructor"), w.user("admin")
    mid = m.received(u)
    clean_key = f"{new_id()}/{new_id()}"
    to_clean = f"UPDATE media_assets SET state = 'clean', clean_key = '{clean_key}' WHERE id = '{mid}'"
    err = m.fails(scanner, to_clean)  # quarantined → clean directement : interdit
    assert "nl:immutable" in err or "interdite" in err
    assert "tentative suivante" in m.fails(scanner, f"UPDATE media_assets SET state = 'scanning' WHERE id = '{mid}'")
    m.run(scanner, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'")
    assert "traçable" in m.fails(scanner, to_clean)  # aucun verdict enregistré
    assert "autre tentative" in m.fails(scanner, scan_event_sql(mid, 2))
    assert "ck_media_scan_clean_traceable" in m.fails(
        scanner, scan_event_sql(mid, 1, age="49 hours")
    )  # signatures périmées
    assert "media:scan" in m.fails(admin, scan_event_sql(mid, 1))  # administrateur : pas de verdict
    m.run(scanner, scan_event_sql(mid, 1, sha="b" * 64))  # verdict sur une autre empreinte
    assert "empreinte" in m.fails(scanner, to_clean)
    m.run(scanner, scan_event_sql(mid, 1, result="error"))
    assert "traçable" in m.fails(scanner, to_clean)  # dernier verdict = erreur
    m.run(scanner, f"UPDATE media_assets SET state = 'scan_failed' WHERE id = '{mid}'")
    m.run(scanner, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 2 WHERE id = '{mid}'")
    m.run(scanner, scan_event_sql(mid, 2))
    assert "media:scan" in m.fails(admin, to_clean)  # même avec un verdict valide, l'administrateur ne peut pas
    m.run(scanner, to_clean)
    assert m.state(mid) == "clean"
    scanned_by = d.sql(f"SELECT DISTINCT scanned_by FROM media_scan_events WHERE media_id = '{mid}'")
    assert scanned_by == scanner  # identité imposée par la base, jamais celle fournie
    # Un média sain ne revient pas en arrière ; détection a posteriori : infecté + époque incrémentée.
    assert "interdite" in m.fails(scanner, f"UPDATE media_assets SET state = 'quarantined' WHERE id = '{mid}'")
    before = m.epoch(mid)
    m.run(scanner, scan_event_sql(mid, 2, result="infected"))
    m.run(scanner, f"UPDATE media_assets SET state = 'infected' WHERE id = '{mid}'")
    assert m.state(mid) == "infected" and m.epoch(mid) == before + 1


# =============================================================================================
# 7–9. Quotas : réservations concurrentes, dépassements, expiration, libération
# =============================================================================================
def test_reservation_and_reception_accounting(m, w, db):
    org = w.org()
    u = w.user("instructor", org=org)
    mid = m.reserve(u, org=org, size=4 * MIB)
    assert m.usage("user", u) == (0, 4 * MIB, GIB)
    assert m.usage("organization", org) == (0, 4 * MIB, 5 * GIB)
    m.run(u, m.receive_sql(mid, size=3 * MIB))  # reçu plus petit que déclaré : reliquat libéré
    assert m.usage("user", u) == (3 * MIB, 0, GIB)
    assert m.usage("organization", org) == (3 * MIB, 0, 5 * GIB)
    assert "ck_media_assets_size" in m.fails(u, m.receive_sql(m.reserve(u, org=org, size=10), size=11))


def test_quota_exceeded_without_overshoot(m, w, db):
    u = w.user("instructor")
    for _ in range(5):
        m.reserve(u, kind="video", size=200 * MIB)
    err = m.fails(u, m.reserve_sql(new_id(), u, kind="video", size=200 * MIB))
    assert "Quota de stockage dépassé" in err
    used, reserved, quota = m.usage("user", u)
    assert (used, reserved, quota) == (0, 1000 * MIB, GIB) and used + reserved <= quota


def test_concurrent_reservations_never_exceed_the_quota_then_release(m, w, db):
    org = w.org()
    admin = w.user("admin")
    users = [w.user("instructor", org=org) for _ in range(8)]
    m.reserve(users[0], org=org, size=1)  # crée la ligne de l'établissement
    m.run(admin, f"UPDATE media_usage SET quota_bytes = {3 * MIB + 1} WHERE organization_id = '{org}'")

    def attempt(user: str) -> tuple[int, str]:
        mid = new_id()
        sql = m.ed._tx(user, m.reserve_sql(mid, user, org=org, size=MIB), None)
        return subprocess.run(db.base + ["-d", db.name, "-c", sql], capture_output=True, text=True).returncode, mid

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, users))
    accepted = [(u, mid) for u, (code, mid) in zip(users, results, strict=True) if code == 0]
    assert len(accepted) == 3 and len(results) == 8
    used, reserved, quota = m.usage("organization", org)
    assert used + reserved == 3 * MIB + 1 and used + reserved <= quota
    # Libération contrôlée (abandon) : la place rendue est de nouveau réservable, une seule fois.
    user, mid = accepted[0]
    m.run(user, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'upload_aborted' WHERE id = '{mid}'")
    with ThreadPoolExecutor(max_workers=4) as pool:
        again = list(pool.map(attempt, users[3:7]))
    assert [code for code, _ in again].count(0) == 1
    assert sum(m.usage("organization", org)[:2]) == 3 * MIB + 1


def test_reservation_expiry_abort_and_controlled_release(m, w, db):
    u = w.user("instructor")
    soon = m.reserve(u, size=2 * MIB, ttl="1 second")
    later = m.reserve(u, size=MIB)
    assert "non échue" in m.fails(
        None, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'reservation_expired' WHERE id = '{later}'"
    )
    time.sleep(1.2)
    assert "expirée" in m.fails(u, m.receive_sql(soon))
    assert m.usage("user", u) == (0, MIB, GIB)  # une réservation échue ne compte plus
    assert db.sql("SELECT nl_media_expire_reservations()") == "1"  # expiration explicite, tracée
    assert db.sql(f"SELECT deleted_reason FROM media_assets WHERE id = '{soon}'") == "reservation_expired"
    assert m.usage("user", u) == (0, MIB, GIB)
    m.run(u, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'upload_aborted' WHERE id = '{later}'")
    assert m.usage("user", u) == (0, 0, GIB)
    received = m.received(u)
    assert "avant réception" in m.fails(
        u, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'upload_aborted' WHERE id = '{received}'"
    )
    assert "irréversible" in m.fails(
        u, f"UPDATE media_assets SET deleted_at = NULL, deleted_reason = NULL WHERE id = '{later}'"
    )


def test_quota_rows_cannot_be_forged_and_quota_changes_are_audited(m, w, db):
    u, admin, instructor = w.user("instructor"), w.user("admin"), w.user("instructor")
    m.reserve(u, size=MIB)
    row = f"WHERE user_id = '{u}'"
    # Aucun compteur stocké : l'occupation est dérivée des médias (rien à falsifier).
    assert (
        db.sql(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'media_usage' "
            "AND column_name IN ('used_bytes', 'reserved_bytes')"
        )
        == "0"
    )
    assert "défaut" in m.fails(
        admin, f"INSERT INTO media_usage (scope_kind, user_id, quota_bytes) VALUES ('user', '{w.user()}', {100 * GIB})"
    )
    assert "interdite" in m.fails(admin, f"DELETE FROM media_usage {row}")
    m.fails(admin, "TRUNCATE media_usage")
    assert "immuable" in m.fails(admin, f"UPDATE media_usage SET user_id = '{admin}' {row}")
    assert "administration" in m.fails(instructor, f"UPDATE media_usage SET quota_bytes = {2 * GIB} {row}")
    assert "acteur" in m.fails(None, f"UPDATE media_usage SET quota_bytes = {2 * GIB} {row}")
    assert "occupation actuelle" in m.fails(admin, f"UPDATE media_usage SET quota_bytes = 10 {row}")
    m.run(admin, f"UPDATE media_usage SET quota_bytes = {2 * GIB} {row}")
    audit = db.sql(
        "SELECT actor_user_id || '|' || (metadata->>'old_quota_bytes') || '|' || (metadata->>'new_quota_bytes') "
        f"FROM audit_logs WHERE action = 'media.quota_changed' AND entity_id = (SELECT id::text FROM media_usage {row})"
    )
    assert audit == f"{admin}|{GIB}|{2 * GIB}"
    assert m.usage("user", u) == (0, MIB, 2 * GIB)


def test_binary_quota_units(db):
    assert db.sql("SELECT nl_media_default_quota('user')") == "1073741824"  # 1 Gio
    assert db.sql("SELECT nl_media_default_quota('organization')") == str(5 * 1073741824)  # 5 Gio
    assert db.sql("SELECT nl_media_default_quota('platform')") == str(20 * 1073741824)  # 20 Gio
    assert db.sql(
        "SELECT nl_media_max_bytes('image') || '|' || nl_media_max_bytes('document') || '|' || "
        "nl_media_max_bytes('captions') || '|' || nl_media_max_bytes('video')"
    ) == (f"{5 * MIB}|{20 * MIB}|{MIB}|{200 * MIB}")


ADVERSARIAL = """
BEGIN;
SELECT set_config('nl.actor_id', '{actor}', true);
CREATE TEMP TABLE adv_probe (x int);
CREATE FUNCTION pg_temp.adv_fn() RETURNS trigger LANGUAGE plpgsql AS $adv$
BEGIN
  {payload};
  RETURN NEW;
END $adv$;
CREATE TRIGGER adv_trg AFTER INSERT ON adv_probe FOR EACH ROW EXECUTE FUNCTION pg_temp.adv_fn();
INSERT INTO adv_probe VALUES (1);
ROLLBACK;
"""


def test_adversarial_nested_triggers_cannot_bypass_quota_or_clean(m, w, db):
    """Un déclencheur imbriqué (profondeur ≥ 2), créable avec le seul privilège TEMP, n'obtient
    aucun droit supplémentaire : aucune règle ne dépend de pg_trigger_depth(). Transactions annulées."""
    u, instructor = w.user("instructor"), w.user("instructor")
    mid = m.received(u)
    m.reserve(u, size=MIB)
    before = (m.usage("user", u), m.state(mid), db.sql("SELECT count(*) FROM media_usage"))
    payloads = {
        f"UPDATE media_usage SET quota_bytes = {100 * GIB} WHERE user_id = '{u}'": "administration",
        f"INSERT INTO media_usage (scope_kind, user_id, quota_bytes) VALUES ('user', '{w.user()}', {100 * GIB})": "défaut",
        f"DELETE FROM media_usage WHERE user_id = '{u}'": "interdite",
        f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'": "media:scan",
        f"UPDATE media_assets SET purged_at = now() WHERE id = '{mid}'": "Purge réservée",
        f"UPDATE media_assets SET byte_size = 1 WHERE id = '{mid}'": "immuable",
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'instructor'": "media_scanner",
        f"PERFORM nl_media_reserve('user', '{u}', NULL, -{GIB})": None,  # n'écrit rien : ne libère rien
    }
    for payload, expected in payloads.items():
        sql = ADVERSARIAL.format(actor=instructor, payload=payload)
        if expected is None:
            db.sql(sql)
        else:
            assert expected in db.fails(sql), payload
    assert (m.usage("user", u), m.state(mid), db.sql("SELECT count(*) FROM media_usage")) == before


# =============================================================================================
# 10–14. Références protégées, conservation légale, signalements, isolation, historique
# =============================================================================================
def test_attachments_drafts_only_and_submission_requires_clean_media(m, w, db):
    a, r = w.user("instructor"), w.user("medical_reviewer")
    _, (lesson,) = w.module()
    lv = m.ed.lesson_version(lesson, a)
    mid = m.received(a)
    attach = (
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv}', '{mid}', '{r}')"
    )
    assert "content:create" in m.fails(r, attach)
    m.run(a, attach)
    assert db.sql(f"SELECT attached_by FROM lesson_version_media WHERE media_id = '{mid}'") == a  # imposé par la base
    m.ed.cite_source(lv)
    err = m.ed.fails(a, f"UPDATE lesson_versions SET status = 'medical_review' WHERE id = '{lv}'")
    assert "nl:media_not_clean" in err or "antivirus" in err
    assert "rattaché" in m.fails(
        a, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{mid}'"
    )
    m.run(a, f"DELETE FROM lesson_version_media WHERE media_id = '{mid}'")  # détachement d'un brouillon
    m.ed.submit(lv, a)  # sans média, le workflow 0005/0006 est inchangé
    assert "brouillon" in m.fails(a, attach)
    pending = m.reserve(a)
    assert "non rattachable" in m.fails(
        a,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES "
        f"('{m.ed.lesson_version(lesson, a)}', '{pending}', '{a}')",
    )


def test_protected_references_survive(scratch):
    d = scratch("protect")
    w, m = World(d), Media(d)
    scanner = grant_test_scanner(d, w)
    a, r = w.user("instructor"), w.user("medical_reviewer")
    _, (lesson,) = w.module()
    lv = m.ed.lesson_version(lesson, a)
    mid = m.received(a)
    m.run(scanner, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'")
    m.run(scanner, scan_event_sql(mid, 1))
    m.run(scanner, f"UPDATE media_assets SET state = 'clean', clean_key = '{new_id()}/{new_id()}' WHERE id = '{mid}'")
    m.run(
        a,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv}', '{mid}', '{a}')",
    )
    m.ed.verified_source(lv, r)
    m.ed.submit(lv, a)  # média sain : soumission acceptée
    m.ed.approve(lv, r)
    assert "protégée" in m.fails(a, f"DELETE FROM lesson_version_media WHERE media_id = '{mid}'")
    assert "rattaché" in m.fails(
        a, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{mid}'"
    )
    assert "Suppression physique" in m.fails(a, f"DELETE FROM media_assets WHERE id = '{mid}'")
    assert "Suppression physique" in m.fails(scanner, f"DELETE FROM media_assets WHERE id = '{mid}'")
    # Une vidéo sans sous-titres sains bloque la soumission (WCAG 1.2.2).
    lv2 = m.ed.lesson_version(lesson, a)
    video = m.received(a, kind="video", size=5000)
    m.run(scanner, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{video}'")
    m.run(scanner, scan_event_sql(video, 1))
    m.run(scanner, f"UPDATE media_assets SET state = 'clean', clean_key = '{new_id()}/{new_id()}' WHERE id = '{video}'")
    m.run(
        a,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv2}', '{video}', '{a}')",
    )
    m.ed.verified_source(lv2, r)
    assert "sous-titres" in m.ed.fails(a, f"UPDATE lesson_versions SET status = 'medical_review' WHERE id = '{lv2}'")
    # Révocation après approbation : la publication éventuelle de la version serait refusée (fermé).
    m.run(r, f"UPDATE media_assets SET state = 'revoked', revoked_reason = 'Révocation de test' WHERE id = '{mid}'")
    assert d.sql(f"SELECT count(*) FROM lesson_version_media WHERE media_id = '{mid}'") == "1"


def test_legal_hold_blocks_deletion_and_purge(m, w, db):
    u, admin, reviewer = w.user("instructor"), w.user("admin"), w.user("medical_reviewer")
    mid = m.received(u)
    hold = f"UPDATE media_assets SET legal_hold = true, legal_hold_reason = 'Enquête fictive (test)' WHERE id = '{mid}'"
    assert "administration" in m.fails(reviewer, hold)
    m.run(admin, hold)
    assert "conservation légale" in m.fails(
        u, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{mid}'"
    )
    m.run(
        reviewer, f"UPDATE media_assets SET state = 'revoked', revoked_reason = 'Révocation de test' WHERE id = '{mid}'"
    )
    assert "conservation légale" in m.fails(admin, f"UPDATE media_assets SET purged_at = now() WHERE id = '{mid}'")
    assert (
        db.sql(f"SELECT count(*) FROM audit_logs WHERE action = 'media.legal_hold_set' AND entity_id = '{mid}'") == "1"
    )
    m.run(admin, f"UPDATE media_assets SET legal_hold = false, legal_hold_reason = NULL WHERE id = '{mid}'")
    assert (
        db.sql(f"SELECT count(*) FROM audit_logs WHERE action = 'media.legal_hold_released' AND entity_id = '{mid}'")
        == "1"
    )
    used_before = m.usage("user", u)[0]
    m.run(admin, f"UPDATE media_assets SET purged_at = now() WHERE id = '{mid}'")  # libération contrôlée
    assert m.usage("user", u)[0] == used_before - 900
    assert "purgé" in m.fails(
        admin, f"UPDATE media_assets SET legal_hold = true, legal_hold_reason = 'Trop tard' WHERE id = '{mid}'"
    )
    assert db.sql(f"SELECT count(*) FROM media_assets WHERE id = '{mid}'") == "1"  # la ligne reste (historique)


def test_reported_media_cannot_be_deleted_or_purged(m, w, db):
    a, admin = w.user("instructor"), w.user("admin")
    _, (lesson,) = w.module()
    lv = m.ed.lesson_version(lesson, a)
    mid = m.received(a)
    m.run(
        a,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv}', '{mid}', '{a}')",
    )
    db.sql(
        "INSERT INTO content_issue_reports (lesson_version_id, category, description) "
        f"VALUES ('{lv}', 'other', 'Signalement fictif (test)')"
    )
    assert "signalement" in m.fails(
        a, f"UPDATE media_assets SET deleted_at = now(), deleted_reason = 'user_request' WHERE id = '{mid}'"
    )
    assert "signalement" in m.fails(admin, f"UPDATE media_assets SET purged_at = now() WHERE id = '{mid}'")


def test_establishment_isolation(m, w, db):
    org_a, org_b = w.org(), w.org()
    ia, ib = w.user("instructor", org=org_a), w.user("instructor", org=org_b)
    assert "content:create" in m.fails(ia, m.reserve_sql(new_id(), ia, org=org_b))
    assert "content:create" in m.fails(ia, m.reserve_sql(new_id(), ia))  # catalogue public : permission globale
    media_a = m.received(ia, org=org_a)
    _, (lesson_b,) = w.module(org=org_b)
    lv_b = m.ed.lesson_version(lesson_b, ib)
    err = m.fails(
        ib,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv_b}', '{media_a}', '{ib}')",
    )
    assert "autre établissement" in err
    global_author = w.user("instructor")
    _, (public_lesson,) = w.module()
    lv_pub = m.ed.lesson_version(public_lesson, global_author)
    assert "autre établissement" in m.fails(
        global_author,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv_pub}', '{media_a}', '{global_author}')",
    )
    public_media = m.received(global_author)
    _, (lesson_a,) = w.module(org=org_a)
    lv_a = m.ed.lesson_version(lesson_a, ia)
    m.run(
        ia,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv_a}', '{public_media}', '{ia}')",
    )
    video_b = m.received(ib, org=org_b, kind="video", size=5000)
    assert "incohérente" in m.fails(ia, m.reserve_sql(new_id(), ia, org=org_a, kind="captions", captions_for=video_b))


def test_history_is_append_only_and_kept(scratch):
    d = scratch("history")
    w, m = World(d), Media(d)
    scanner = grant_test_scanner(d, w)
    a = w.user("instructor")
    mid = m.received(a)
    m.run(scanner, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = '{mid}'")
    m.run(scanner, scan_event_sql(mid, 1, result="infected"))
    m.run(scanner, f"UPDATE media_assets SET state = 'infected' WHERE id = '{mid}'")
    assert "ajout seul" in m.fails(scanner, f"UPDATE media_scan_events SET result = 'clean' WHERE media_id = '{mid}'")
    assert "ajout seul" in m.fails(scanner, f"DELETE FROM media_scan_events WHERE media_id = '{mid}'")
    m.fails(scanner, "TRUNCATE media_scan_events")
    m.fails(scanner, "TRUNCATE media_assets CASCADE")
    # Suppression d'un brouillon de leçon : ses rattachements suivent, le média et ses analyses restent.
    _, (lesson,) = w.module()
    lv = m.ed.lesson_version(lesson, a)
    kept = m.received(a)
    m.run(
        a,
        f"INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES ('{lv}', '{kept}', '{a}')",
    )
    m.run(a, f"DELETE FROM lesson_versions WHERE id = '{lv}'")
    assert d.sql(f"SELECT count(*) FROM media_assets WHERE id = '{kept}'") == "1"
    m.run(scanner, f"UPDATE media_assets SET purged_at = now() WHERE id = '{mid}'")
    assert d.sql(f"SELECT count(*) FROM media_scan_events WHERE media_id = '{mid}'") == "1"
    assert "purgé" in m.fails(
        scanner, f"UPDATE media_assets SET state = 'scanning', scan_attempt = 2 WHERE id = '{mid}'"
    )


# =============================================================================================
# 15. Compatibilité : 0006 → 0007 avec données historiques ; retour arrière sûr ou refusé
# =============================================================================================
def test_upgrade_from_0006_with_historical_data_then_safe_downgrade(scratch):
    d = scratch("compat", upto="0006_independent_program_versions")
    w, ed = World(d), EditorialV2(d)
    a, r = w.user("instructor"), w.user("medical_reviewer")
    program = w.program(medical=False)
    module, (lesson,) = w.module()
    lv = ed.approved_version(lesson, a, r, sources=1)
    mv = ed.approved_module_version(module, a, r, [(lesson, {"fr": lv})])
    rel = ed.release_v2(program, a, [(module, mv)])
    ed.publish_release(rel, a, r)
    d.sql(
        "INSERT INTO learning_resources (lesson_id, kind, title, external_url, license) VALUES "
        f"('{lesson}', 'link', 'Ressource historique', 'https://example.test/r', 'CC-BY-4.0')"
    )
    counts = "SELECT (SELECT count(*) FROM program_releases) || '|' || (SELECT count(*) FROM lesson_versions) || '|' || (SELECT count(*) FROM learning_resources) || '|' || (SELECT count(*) FROM editorial_decisions)"
    before, signature_0006 = d.sql(counts), _signature(d)
    d.file(UP)
    assert d.sql(counts) == before
    assert d.sql(f"SELECT status FROM program_releases WHERE id = '{rel}'") == "published"
    # Le workflow existant reste opérant (aucun média rattaché).
    lv2 = ed.approved_version(lesson, a, r, sources=1)
    assert ed.status("lesson_versions", lv2) == "approved"
    d.file(DOWN)  # aucune donnée 0007 : retour arrière exact
    assert _signature(d) == signature_0006 and d.sql(counts) != ""
    d.file(UP)
    m = Media(d)
    m.reserve(a)
    with pytest.raises(Exception) as exc:
        d.file(DOWN)
    assert "Retour arrière 0007 refusé" in str(exc.value)
    assert d.sql("SELECT count(*) FROM media_assets") == "1"


def test_empty_database_up_down_up_is_exact(scratch):
    d = scratch("empty", upto="0006_independent_program_versions", seeds=False)
    before = _signature(d)
    d.file(UP)
    after_up = _signature(d)
    d.file(DOWN)
    assert _signature(d) == before
    d.file(UP)
    assert _signature(d) == after_up


def test_downgrade_refused_when_a_role_holds_media_scan(scratch):
    d = scratch("role")
    grant_test_scanner(d, World(d))
    with pytest.raises(Exception) as exc:
        d.file(DOWN)
    assert "rôles media:scan=1" in str(exc.value)


def test_existing_installation_loses_unauthorized_media_scan_grants(scratch):
    """Installation existante où media:scan aurait été créée et donnée à admin hors migration :
    0007 retire l'attribution, pose la garde, et les données de référence ne la redonnent pas."""
    d = scratch("existing", upto="0006_independent_program_versions")
    d.sql("INSERT INTO permissions (code, description) VALUES ('media:scan', 'créée hors migration (test)')")
    d.sql(
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code IN ('admin', 'medical_reviewer')"
    )
    assert d.sql("SELECT count(*) FROM role_permissions WHERE permission_code = 'media:scan'") == "2"
    d.file(UP)
    assert d.sql("SELECT count(*) FROM role_permissions WHERE permission_code = 'media:scan'") == "0"
    d.file(SCHEMA.parent / "seeds" / "0001_reference_data.sql")
    d.file(SCHEMA.parent / "seeds" / "0001_reference_data.sql")
    assert d.sql("SELECT count(*) FROM role_permissions WHERE permission_code = 'media:scan'") == "0"
    assert "media_scanner" in d.fails(
        "INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'admin'"
    )

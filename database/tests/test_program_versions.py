"""Migration 0006 (étape 2.2.2-a, D12, ADR-0010) : versions de modules indépendantes par édition.

Vérifié sur PostgreSQL réel : clés étrangères composées, déclencheurs, immuabilité, workflow
éditorial des versions de modules, sources vérifiées, indépendance des programmes, isolation,
historique des inscriptions, compatibilité avec les éditions 0005 et retour arrière sûr.
"""

from __future__ import annotations

import os

import pytest
from conftest import SCHEMA, Db, SqlError, build_db, new_id
from editorial import Editorial, EditorialV2, q

UP = SCHEMA / "0006_independent_program_versions.sql"
DOWN = SCHEMA / "0006_independent_program_versions_down.sql"


# --------------------------------------------------------------------------- monde de test
class World:
    """Données de test créées dans n'importe quelle base (tête ou base jetable)."""

    def __init__(self, db: Db):
        self.db = db
        self.spec = db.sql("SELECT id FROM specialties WHERE code = 'care_assistant_core'")

    def user(self, *roles: str, org: str | None = None) -> str:
        uid = new_id()
        self.db.sql(
            f"INSERT INTO users (id, email, status, email_verified_at) "
            f"VALUES ('{uid}', '{uid[:8]}@example.test', 'active', now())"
        )
        for role in roles:
            org_sql = f"'{org}'" if org and role != "medical_reviewer" else "NULL"
            self.db.sql(
                "BEGIN; SELECT set_config('nl.role_grant_bypass', 'on', true); "
                f"INSERT INTO user_roles (user_id, role_id, organization_id) "
                f"SELECT '{uid}', id, {org_sql} FROM roles WHERE code = '{role}'; COMMIT;"
            )
        return uid

    def org(self) -> str:
        oid = new_id()
        self.db.sql(
            f"INSERT INTO organizations (id, slug, name, kind) VALUES ('{oid}', 'org-{oid[:8]}', 'Institut {oid[:4]}', 'school')"
        )
        return oid

    def program(self, org: str | None = None, medical: bool = True) -> str:
        pid = new_id()
        org_sql = f"'{org}'" if org else "NULL"
        tier = "organization" if org else "free"
        self.db.sql(
            "INSERT INTO programs (id, code, profession_id, specialty_id, organization_id, title_i18n, level, "
            f"access_tier, is_medical) SELECT '{pid}', 'p-{pid[:8]}', p.id, '{self.spec}', {org_sql}, "
            f"'{{\"fr\":\"Programme\"}}', 'beginner', '{tier}', {str(medical).lower()} FROM professions p "
            "JOIN jurisdictions j ON j.id = p.jurisdiction_id WHERE j.code = 'CA-QC' AND p.code = 'pab'"
        )
        return pid

    def module(self, org: str | None = None, lessons: int = 1) -> tuple[str, list[str]]:
        mid = new_id()
        org_sql = f"'{org}'" if org else "NULL"
        self.db.sql(
            f"INSERT INTO modules (id, code, specialty_id, organization_id, level, title_i18n) VALUES "
            f"('{mid}', 'm-{mid[:8]}', '{self.spec}', {org_sql}, 'beginner', '{{\"fr\":\"Module\"}}')"
        )
        ids = []
        for i in range(lessons):
            lid = new_id()
            self.db.sql(
                f"INSERT INTO lessons (id, module_id, code, position, kind) VALUES ('{lid}', '{mid}', 'l{i}', {i}, 'theory')"
            )
            ids.append(lid)
        return mid, ids

    def student(self) -> str:
        return self.user("student")

    def enroll(self, user: str, program: str, release: str, org: str | None = None) -> str:
        eid = new_id()
        self.db.sql(
            "INSERT INTO enrollments (id, user_id, program_id, release_id, organization_id) VALUES "
            f"('{eid}', '{user}', '{program}', '{release}', {q(org) if org else 'NULL'})"
        )
        return eid


def event_sql(enrollment, user, release, kind, module, lesson, version, module_version=None, block=None, cid=None):
    cols = {
        "enrollment_id": enrollment,
        "user_id": user,
        "release_id": release,
        "client_event_id": cid or new_id(),
        "event_type": kind,
        "module_id": module,
        "lesson_id": lesson,
        "lesson_version_id": version,
    }
    if module_version:
        cols["module_version_id"] = module_version
    if block:
        cols["block_id"] = block
    values = ", ".join(q(v) for v in cols.values())
    return f"INSERT INTO learning_events ({', '.join(cols)}, occurred_at) VALUES ({values}, now())"


@pytest.fixture
def w(db) -> World:
    return World(db)


@pytest.fixture
def ed(db) -> EditorialV2:
    return EditorialV2(db)


@pytest.fixture
def people(w):
    return {
        "author": w.user("instructor"),
        "reviewer": w.user("medical_reviewer"),
        "reviewer2": w.user("medical_reviewer"),
    }


@pytest.fixture
def course(w, ed, people):
    """Programme médical public, module à une leçon, versions FR/EN approuvées, version de module approuvée."""
    a, r = people["author"], people["reviewer"]
    program = w.program()
    module, (lesson,) = w.module()
    fr = ed.approved_version(lesson, a, r, "fr")
    en = ed.approved_version(lesson, a, r, "en")
    mv = ed.approved_module_version(module, a, r, [(lesson, {"fr": fr, "en": en})])
    return {"program": program, "module": module, "lesson": lesson, "fr": fr, "en": en, "mv": mv}


@pytest.fixture
def published(ed, course, people):
    rid = ed.release_v2(course["program"], people["author"], [(course["module"], course["mv"])])
    ed.publish_release(rid, people["author"], people["reviewer"])
    return {**course, "release": rid}


# =========================================================================== schéma
def test_structures_and_constraints_exist(db):
    tables = db.sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_name IN "
        "('module_versions','module_version_lessons','module_version_lesson_versions','program_release_modules',"
        "'program_release_prerequisites')"
    )
    assert tables == (
        "module_version_lesson_versions,module_version_lessons,module_versions,"
        "program_release_modules,program_release_prerequisites"
    )
    assert db.sql("SELECT count(*) FROM pg_indexes WHERE indexname = 'ux_lesson_versions_one_published'") == "0"
    assert "module_version" in db.sql(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_editorial_decisions_entity_type'"
    )
    assert (
        db.sql(
            "SELECT column_default FROM information_schema.columns WHERE table_name = 'program_releases' "
            "AND column_name = 'structure'"
        )
        == "'module_versions'::text"
    )
    fks = db.sql(
        "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid::regclass::text IN "
        "('module_version_lessons','module_version_lesson_versions','program_release_modules',"
        "'program_release_prerequisites')"
    )
    assert int(fks) >= 7


def test_new_editions_must_cite_module_versions(db, ed, course, people):
    a = people["author"]
    assert "structure « module_versions » obligatoire" in db.fails(
        "INSERT INTO program_releases (program_id, release_no, author_id, structure) "
        f"VALUES ('{course['program']}', 1, '{a}', 'manifest_v1')"
    )
    assert "manifeste JSON reste vide" in db.fails(
        "INSERT INTO program_releases (program_id, release_no, author_id, manifest) "
        f"VALUES ('{course['program']}', 1, '{a}', '{{\"modules\":[{{}}]}}')"
    )
    rid = ed.release_v2(course["program"], a, [(course["module"], course["mv"])])
    assert "non modifiable" in db.fails(f"UPDATE program_releases SET structure = 'manifest_v1' WHERE id = '{rid}'")


# =========================================================================== clés étrangères
def test_foreign_keys_forbid_incoherent_links(db, w, ed, course, people):
    a, r = people["author"], people["reviewer"]
    other_module, (other_lesson,) = w.module()
    other_v = ed.approved_version(other_lesson, a, r)
    mv = ed.module_version(course["module"], a, [])
    ins_lesson = "INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position) VALUES ('{mv}', '{m}', '{l}', 0)"
    # Leçon d'un autre module, ou module ≠ module de la version.
    assert "foreign key" in db.fails(ins_lesson.format(mv=mv, m=course["module"], l=other_lesson)).lower()
    assert "foreign key" in db.fails(ins_lesson.format(mv=mv, m=other_module, l=other_lesson)).lower()
    db.sql(ins_lesson.format(mv=mv, m=course["module"], l=course["lesson"]))
    ins_v = (
        "INSERT INTO module_version_lesson_versions (module_version_id, lesson_id, locale, lesson_version_id) "
        "VALUES ('{mv}', '{l}', '{loc}', '{v}')"
    )
    # Version d'une autre leçon, ou langue différente de la version.
    assert "foreign key" in db.fails(ins_v.format(mv=mv, l=course["lesson"], loc="fr", v=other_v)).lower()
    assert "foreign key" in db.fails(ins_v.format(mv=mv, l=course["lesson"], loc="fr", v=course["en"])).lower()
    # Édition : version de module annoncée pour un autre module ; prérequis hors de l'édition.
    rid = ed.release_v2(course["program"], a, [])
    assert (
        "foreign key"
        in db.fails(
            "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position) "
            f"VALUES ('{rid}', '{other_module}', '{course['mv']}', 0)"
        ).lower()
    )
    db.sql(
        "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position) "
        f"VALUES ('{rid}', '{course['module']}', '{course['mv']}', 0)"
    )
    assert (
        "foreign key"
        in db.fails(
            "INSERT INTO program_release_prerequisites (release_id, module_id, prerequisite_module_id) "
            f"VALUES ('{rid}', '{course['module']}', '{other_module}')"
        ).lower()
    )
    assert (
        "check"
        in db.fails(
            "INSERT INTO program_release_prerequisites (release_id, module_id, prerequisite_module_id) "
            f"VALUES ('{rid}', '{course['module']}', '{course['module']}')"
        ).lower()
    )
    # Une version de leçon citée ne peut pas être supprimée.
    draft = ed.lesson_version(course["lesson"], a)
    ed.db.sql(ins_v.format(mv=mv, l=course["lesson"], loc="fr", v=draft))
    assert "foreign key" in db.fails(f"DELETE FROM lesson_versions WHERE id = '{draft}'").lower()


# =========================================================================== versions de modules
def test_module_version_workflow_is_logged(db, w, ed, people):
    a, r = people["author"], people["reviewer"]
    module, (lesson,) = w.module()
    fr = ed.approved_version(lesson, a, r)
    mv = ed.module_version(module, a, [(lesson, {"fr": fr})])
    ed.set_status("module_versions", mv, "medical_review", a)
    ed.set_status("module_versions", mv, "approved", r)
    row = db.sql(f"SELECT status, reviewed_by, content_sha256 IS NOT NULL FROM module_versions WHERE id = '{mv}'")
    assert row == f"approved|{r}|t"
    log = db.sql(
        "SELECT string_agg(decision || ':' || actor_id, ',' ORDER BY id) FROM editorial_decisions "
        f"WHERE entity_type = 'module_version' AND entity_id = '{mv}'"
    )
    assert log == f"submitted:{a},approved:{r}"


def test_module_versions_are_born_draft_and_numbered(db, w, people):
    a, r = people["author"], people["reviewer"]
    module, _ = w.module()
    assert "naît en brouillon" in db.fails(
        f"INSERT INTO module_versions (module_id, version_no, author_id, status) VALUES ('{module}', 1, '{a}', 'approved')"
    )
    assert "trace de relecture" in db.fails(
        f"INSERT INTO module_versions (module_id, version_no, author_id, reviewed_by) VALUES ('{module}', 1, '{a}', '{r}')"
    )
    assert "attendu : 1" in db.fails(
        f"INSERT INTO module_versions (module_id, version_no, author_id) VALUES ('{module}', 3, '{a}')"
    )


def test_module_version_review_rules(db, w, ed, people):
    a, r = people["author"], people["reviewer"]
    module, (lesson, lesson2) = w.module(lessons=2)
    fr = ed.approved_version(lesson, a, r)
    # Version de module vide, leçon sans version française, version de leçon non soumise.
    empty = ed.module_version(module, a, [])
    assert "vide" in ed.fails(a, f"UPDATE module_versions SET status = 'medical_review' WHERE id = '{empty}'")
    en_only = ed.approved_version(lesson2, a, r, "en")
    no_fr = ed.module_version(module, a, [(lesson2, {"en": en_only})])
    assert "version française" in ed.fails(
        a, f"UPDATE module_versions SET status = 'medical_review' WHERE id = '{no_fr}'"
    )
    draft_lv = ed.lesson_version(lesson2, a)
    unsub = ed.module_version(module, a, [(lesson2, {"fr": draft_lv})])
    assert "encore en « draft »" in ed.fails(
        a, f"UPDATE module_versions SET status = 'medical_review' WHERE id = '{unsub}'"
    )
    # Auto-approbation, permission, indépendance vis-à-vis des leçons incluses.
    mv = ed.module_version(module, a, [(lesson, {"fr": fr})])
    ed.set_status("module_versions", mv, "medical_review", a)
    approve = f"UPDATE module_versions SET status = 'approved' WHERE id = '{mv}'"
    assert "content:review" in ed.fails(a, approve)
    assert "content:review" in ed.fails(w.user("instructor"), approve)
    lesson_author_reviewer = w.user("instructor", "medical_reviewer")
    lv2 = ed.approved_version(lesson2, lesson_author_reviewer, r)
    mv2 = ed.module_version(module, a, [(lesson2, {"fr": lv2})])
    ed.set_status("module_versions", mv2, "medical_review", a)
    assert "Indépendance" in ed.fails(
        lesson_author_reviewer, f"UPDATE module_versions SET status = 'approved' WHERE id = '{mv2}'"
    )
    # Demande de modifications motivée.
    assert "motif obligatoire" in ed.fails(r, f"UPDATE module_versions SET status = 'draft' WHERE id = '{mv}'")
    ed.set_status("module_versions", mv, "draft", r, comment="Ajouter la version anglaise")


def test_module_version_content_is_frozen_after_submission(db, w, ed, course, people):
    mv = course["mv"]
    assert "non modifiable" in db.fails(f"DELETE FROM module_version_lesson_versions WHERE module_version_id = '{mv}'")
    assert "non modifiable" in db.fails(
        f"UPDATE module_version_lessons SET required = false WHERE module_version_id = '{mv}'"
    )
    assert "modification directe interdite" in db.fails(
        f"UPDATE module_versions SET change_summary = 'x' WHERE id = '{mv}'"
    )
    assert "non supprimable" in db.fails(f"DELETE FROM module_versions WHERE id = '{mv}'")
    # Une évolution passe par une nouvelle version, soumise à une nouvelle relecture.
    mv2 = ed.module_version(course["module"], people["author"], [(course["lesson"], {"fr": course["fr"]})])
    assert db.sql(f"SELECT version_no, status FROM module_versions WHERE id = '{mv2}'") == "2|draft"


# =========================================================================== sources vérifiées
def test_sources_must_be_active_and_explicitly_verified(db, w, ed, people):
    a, r = people["author"], people["reviewer"]
    program = w.program()
    module, (lesson,) = w.module()
    unverified = ed.approved_version(lesson, a, r, verified=False)
    mv = ed.approved_module_version(module, a, r, [(lesson, {"fr": unverified})])
    rid = ed.release_v2(program, a, [(module, mv)])
    ed.set_status("program_releases", rid, "medical_review", a)
    assert "active et vérifiée" in ed.fails(r, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid}'")
    ref = db.sql(f"SELECT reference_id FROM lesson_version_references WHERE lesson_version_id = '{unverified}'")
    verify = f"UPDATE medical_references SET last_verified_at = current_date WHERE id = '{ref}'"
    assert "sans acteur" in db.fails(verify)
    assert "relecteur habilité" in ed.fails(a, verify)  # un auteur ne vérifie pas
    assert "future" in ed.fails(
        r, f"UPDATE medical_references SET last_verified_at = current_date + 1 WHERE id = '{ref}'"
    )
    assert "sans date" in db.fails(f"UPDATE medical_references SET verified_by = '{r}' WHERE id = '{ref}'")
    ed.run(r, verify)
    assert db.sql(f"SELECT verified_by FROM medical_references WHERE id = '{ref}'") == r
    ed.set_status("program_releases", rid, "approved", r)


def test_non_medical_program_needs_no_source_but_still_review(db, w, ed, people):
    a, r = people["author"], people["reviewer"]
    program = w.program(medical=False)
    module, (lesson,) = w.module()
    lv = ed.approved_version(lesson, a, r, sources=0)
    mv = ed.approved_module_version(module, a, r, [(lesson, {"fr": lv})])
    rid = ed.release_v2(program, a, [(module, mv)])
    ed.set_status("program_releases", rid, "medical_review", a)
    assert "content:review" in ed.fails(a, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid}'")
    ed.set_status("program_releases", rid, "approved", r)


# =========================================================================== éditions relationnelles
def test_release_publication_publishes_cited_versions(db, ed, published, people):
    c = published
    assert ed.status("program_releases", c["release"]) == "published"
    assert ed.status("programs", c["program"]) == "published"
    assert ed.status("modules", c["module"]) == "published"
    assert ed.status("module_versions", c["mv"]) == "published"
    assert ed.status("lesson_versions", c["fr"]) == ed.status("lesson_versions", c["en"]) == "published"
    decisions = db.sql(
        f"SELECT string_agg(decision, ',' ORDER BY id) FROM editorial_decisions WHERE entity_id = '{c['release']}'"
    )
    assert decisions == "submitted,approved,published"


def test_release_content_is_frozen_after_submission(db, ed, course, people):
    rid = ed.release_v2(course["program"], people["author"], [(course["module"], course["mv"])])
    ed.set_status("program_releases", rid, "medical_review", people["author"])
    assert "non modifiable" in db.fails(f"DELETE FROM program_release_modules WHERE release_id = '{rid}'")
    assert "non modifiable" in db.fails(
        f"UPDATE program_release_modules SET required = false WHERE release_id = '{rid}'"
    )


def test_release_requires_approved_module_versions_and_independent_reviewer(db, w, ed, people):
    a, r, r2 = people["author"], people["reviewer"], people["reviewer2"]
    program = w.program()
    module, (lesson,) = w.module()
    fr = ed.approved_version(lesson, a, r)
    draft_mv = ed.module_version(module, a, [(lesson, {"fr": fr})])
    rid = ed.release_v2(program, a, [(module, draft_mv)])
    assert "encore en « draft »" in ed.fails(
        a, f"UPDATE program_releases SET status = 'medical_review' WHERE id = '{rid}'"
    )
    # Version de module rédigée par un relecteur : il ne peut pas approuver l'édition.
    mv_author = w.user("instructor", "medical_reviewer")
    mv = ed.approved_module_version(module, mv_author, r2, [(lesson, {"fr": fr})])
    rid2 = ed.release_v2(w.program(), a, [(module, mv)])
    ed.set_status("program_releases", rid2, "medical_review", a)
    assert "Indépendance" in ed.fails(mv_author, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid2}'")
    ed.set_status("program_releases", rid2, "approved", r2)


def test_prerequisites_are_frozen_and_acyclic(db, w, ed, people):
    a, r = people["author"], people["reviewer"]
    program = w.program()
    (m1, (l1,)), (m2, (l2,)) = w.module(), w.module()
    mv1 = ed.approved_module_version(m1, a, r, [(l1, {"fr": ed.approved_version(l1, a, r)})])
    mv2 = ed.approved_module_version(m2, a, r, [(l2, {"fr": ed.approved_version(l2, a, r)})])
    cyclic = ed.release_v2(program, a, [(m1, mv1), (m2, mv2)], prerequisites=[(m2, m1), (m1, m2)])
    assert "circulaires" in ed.fails(a, f"UPDATE program_releases SET status = 'medical_review' WHERE id = '{cyclic}'")
    db.sql(f"DELETE FROM program_releases WHERE id = '{cyclic}'")
    rid = ed.release_v2(program, a, [(m1, mv1), (m2, mv2)], prerequisites=[(m2, m1)])
    ed.publish_release(rid, a, r)
    assert "non modifiable" in db.fails(f"DELETE FROM program_release_prerequisites WHERE release_id = '{rid}'")
    assert db.sql(f"SELECT prerequisite_module_id FROM program_release_prerequisites WHERE release_id = '{rid}'") == m1


def test_organization_isolation_of_module_versions(db, w, ed, people):
    a, r = people["author"], people["reviewer"]
    org_a, org_b = w.org(), w.org()
    mod_b, (lesson_b,) = w.module(org=org_b)
    author_b = w.user("instructor", org=org_b)
    lv = ed.approved_version(lesson_b, author_b, r)
    mv_b = ed.approved_module_version(mod_b, author_b, r, [(lesson_b, {"fr": lv})])
    rid = ed.release_v2(w.program(org=org_a), a, [])
    assert "hors de l'établissement" in db.fails(
        "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position) "
        f"VALUES ('{rid}', '{mod_b}', '{mv_b}', 0)"
    )
    # Un formateur d'un autre établissement ne soumet pas la version de module de B.
    mv_draft = ed.module_version(mod_b, author_b, [(lesson_b, {"fr": lv})])
    assert "content:create" in ed.fails(
        w.user("instructor", org=org_a), f"UPDATE module_versions SET status = 'medical_review' WHERE id = '{mv_draft}'"
    )
    ed.set_status("module_versions", mv_draft, "medical_review", author_b)
    ed.db.sql(
        "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position) "
        f"SELECT '{ed.release_v2(w.program(org=org_b), author_b, [])}', '{mod_b}', '{mv_b}', 0"
    )


# =========================================================================== indépendance des programmes
def test_two_programs_follow_different_versions_of_the_same_module(db, w, ed, published, people):
    c, a, r = published, people["author"], people["reviewer"]
    student_1 = w.student()
    e1 = w.enroll(student_1, c["program"], c["release"])
    # Programme B : même module, nouvelle version de la leçon et nouvelle version du module.
    fr2 = ed.approved_version(c["lesson"], a, r, "fr")
    mv2 = ed.approved_module_version(c["module"], a, r, [(c["lesson"], {"fr": fr2, "en": c["en"]})])
    program_b = w.program()
    rel_b = ed.release_v2(program_b, a, [(c["module"], mv2)])
    ed.publish_release(rel_b, a, r)
    # Rien n'a changé pour le programme A.
    assert ed.status("program_releases", c["release"]) == "published"
    assert ed.status("module_versions", c["mv"]) == "published"
    assert ed.status("lesson_versions", c["fr"]) == "published"
    assert ed.status("lesson_versions", fr2) == "published"  # deux versions publiées coexistent
    assert db.sql(f"SELECT release_id FROM enrollments WHERE id = '{e1}'") == c["release"]
    # Le programme A publie ensuite la nouvelle version : B n'est pas touché, l'inscription reste.
    rel_a2 = ed.release_v2(c["program"], a, [(c["module"], mv2)])
    ed.publish_release(rel_a2, a, r)
    assert ed.status("program_releases", c["release"]) == "archived"
    assert ed.status("program_releases", rel_b) == "published"
    assert db.sql(f"SELECT release_id FROM enrollments WHERE id = '{e1}'") == c["release"]  # aucun changement implicite
    db.sql(f"UPDATE enrollments SET release_id = '{rel_a2}' WHERE id = '{e1}'")  # changement explicite
    assert "plus récente" in db.fails(f"UPDATE enrollments SET release_id = '{c['release']}' WHERE id = '{e1}'")


def test_versions_in_use_by_a_published_release_cannot_be_archived(db, w, ed, published, people):
    c, r = published, people["reviewer"]
    assert "archivage refusé" in ed.fails(r, f"UPDATE lesson_versions SET status = 'archived' WHERE id = '{c['fr']}'")
    assert "archivage refusé" in ed.fails(r, f"UPDATE module_versions SET status = 'archived' WHERE id = '{c['mv']}'")
    ed.set_status("program_releases", c["release"], "archived", r)
    ed.set_status("module_versions", c["mv"], "archived", r)
    ed.set_status("lesson_versions", c["fr"], "archived", r)
    assert ed.status("programs", c["program"]) == "archived"


# =========================================================================== inscriptions et historique
def test_events_and_progress_track_the_module_version(db, w, ed, published, people):
    c = published
    student = w.student()
    eid = w.enroll(student, c["program"], c["release"])
    base = (eid, student, c["release"], "block_reached", c["module"], c["lesson"], c["fr"])
    assert "version de module obligatoire" in db.fails(event_sql(*base, block="b1"))
    other_mv = ed.module_version(c["module"], people["author"], [])
    assert "hors de l'édition" in db.fails(event_sql(*base, module_version=other_mv, block="b1"))
    cid = new_id()
    for _ in range(2):  # idempotence
        db.sql(
            event_sql(*base, module_version=c["mv"], block="b1", cid=cid)
            + " ON CONFLICT (enrollment_id, client_event_id) DO NOTHING"
        )
    assert db.sql(f"SELECT count(*) FROM learning_events WHERE enrollment_id = '{eid}'") == "1"
    assert "ajout seul" in db.fails(f"UPDATE learning_events SET block_id = 'b0' WHERE enrollment_id = '{eid}'")
    db.sql(
        "INSERT INTO student_progress (user_id, lesson_id, enrollment_id, lesson_version_id, module_version_id, "
        f"last_block_id) VALUES ('{student}', '{c['lesson']}', '{eid}', '{c['fr']}', '{c['mv']}', 'b1')"
    )
    other = w.student()
    e2 = w.enroll(other, c["program"], c["release"])
    assert "hors des éditions" in db.fails(
        "INSERT INTO student_progress (user_id, lesson_id, enrollment_id, lesson_version_id, module_version_id) "
        f"VALUES ('{other}', '{c['lesson']}', '{e2}', '{c['fr']}', '{other_mv}')"
    )
    db.sql(f"UPDATE enrollments SET last_lesson_id = '{c['lesson']}', last_block_id = 'b1' WHERE id = '{eid}'")


# =========================================================================== compatibilité et retour arrière
def _signature(d: Db) -> str:
    return d.sql(
        "SELECT md5(string_agg(x, '|' ORDER BY x)) FROM ("
        " SELECT 't:' || table_name || '.' || column_name || ':' || data_type || ':' || is_nullable || ':' ||"
        "   coalesce(column_default, '') AS x FROM information_schema.columns WHERE table_schema = 'public'"
        " UNION ALL SELECT 'c:' || conrelid::regclass || ':' || conname || ':' || pg_get_constraintdef(oid) "
        "   FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
        " UNION ALL SELECT 'g:' || tgrelid::regclass || ':' || tgname FROM pg_trigger WHERE NOT tgisinternal"
        " UNION ALL SELECT 'f:' || p.oid::regprocedure || ':' || md5(p.prosrc) FROM pg_proc p"
        "   WHERE pronamespace = 'public'::regnamespace"
        " UNION ALL SELECT 'i:' || indexname || ':' || indexdef FROM pg_indexes WHERE schemaname = 'public') s"
    )


@pytest.fixture
def scratch():
    created: list[Db] = []

    def _make(tag: str, upto: str, seeds: bool = True) -> Db:
        d = build_db(f"nl_m6_{tag}_{os.getpid()}", upto=upto, seeds=seeds)
        created.append(d)
        return d

    yield _make
    for d in created:
        d.admin(f"DROP DATABASE IF EXISTS {d.name} WITH (FORCE)")


def test_empty_database_up_down_up_is_exact(scratch):
    d = scratch("empty", "0005_learning_engine", seeds=False)
    before = _signature(d)
    d.file(UP)
    after = _signature(d)
    d.file(DOWN)
    assert _signature(d) == before
    d.file(UP)
    assert _signature(d) == after


def test_historical_0005_editions_and_enrollments_survive_and_coexist(scratch):
    d = scratch("hist", "0005_learning_engine")
    w, ed = World(d), Editorial(d)
    a, r = w.user("instructor"), w.user("medical_reviewer")
    program = w.program(medical=False)
    module, (lesson,) = w.module()
    fr = ed.approved_version(lesson, a, r, "fr", sources=0)
    rel_v1 = ed.release(program, a, ed.manifest((module, [(lesson, {"fr": fr})])))
    ed.publish_release(rel_v1, a, r)
    student = w.student()
    eid = w.enroll(student, program, rel_v1)
    d.sql(event_sql(eid, student, rel_v1, "lesson_opened", module, lesson, fr))
    before = _signature(d)
    counts = d.sql(
        "SELECT (SELECT count(*) FROM program_releases) || '|' || (SELECT count(*) FROM enrollments) "
        "|| '|' || (SELECT count(*) FROM learning_events) || '|' || (SELECT count(*) FROM editorial_decisions)"
    )

    d.file(UP)
    # L'édition historique reste valide et suivie ; ses événements restent acceptés (sans version de module).
    assert (
        d.sql(f"SELECT structure || '|' || status FROM program_releases WHERE id = '{rel_v1}'")
        == "manifest_v1|published"
    )
    d.sql(event_sql(eid, student, rel_v1, "lesson_completed", module, lesson, fr))
    assert "édition historique" in d.fails(
        event_sql(eid, student, rel_v1, "lesson_opened", module, lesson, fr, module_version=new_id())
    )
    # Retour arrière encore sûr (seulement des données 0005) : schéma restauré, données intactes.
    d.file(DOWN)
    assert _signature(d) == before
    after_down = d.sql(
        "SELECT (SELECT count(*) FROM program_releases) || '|' || (SELECT count(*) FROM enrollments) "
        "|| '|' || (SELECT count(*) FROM learning_events) || '|' || (SELECT count(*) FROM editorial_decisions)"
    )
    released, enrolled, events, decisions = counts.split("|")
    assert after_down == f"{released}|{enrolled}|{int(events) + 1}|{decisions}"

    # Remontée puis nouvelle édition relationnelle du même programme : l'inscription reste sur l'édition initiale.
    d.file(UP)
    ed2 = EditorialV2(d)
    mv = ed2.approved_module_version(module, a, r, [(lesson, {"fr": fr})])
    rel_v2 = ed2.release_v2(program, a, [(module, mv)])
    ed2.publish_release(rel_v2, a, r)
    assert d.sql(f"SELECT status FROM program_releases WHERE id = '{rel_v1}'") == "archived"
    assert d.sql(f"SELECT release_id FROM enrollments WHERE id = '{eid}'") == rel_v1
    # Désormais, le retour arrière doit refuser, sans rien modifier.
    head = _signature(d)
    with pytest.raises(SqlError, match="Retour arrière 0006 refusé"):
        d.file(DOWN)
    assert _signature(d) == head
    assert d.sql("SELECT count(*) FROM module_versions") == "1"


def test_downgrade_refuses_multiple_published_versions_and_verified_sources(scratch):
    d = scratch("dup", "0006_independent_program_versions")
    w, ed = World(d), EditorialV2(d)
    a, r = w.user("instructor"), w.user("medical_reviewer")
    module, (lesson,) = w.module()
    lv = ed.lesson_version(lesson, a)
    ed.verified_source(lv, r)
    with pytest.raises(SqlError, match="sources vérifiées=1"):
        d.file(DOWN)


def test_demo_fixture_runs_through_the_workflow_at_head(scratch):
    d = scratch("demo", "0006_independent_program_versions")
    d._run(["-c", "SET nl.allow_demo = 'on'", "-f", str(SCHEMA.parent / "fixtures" / "e2e_demo_published.sql")])
    assert (
        d.sql(
            "SELECT p.status || '|' || r.structure || '|' || mv.status FROM programs p "
            "JOIN program_releases r ON r.program_id = p.id JOIN program_release_modules rm ON rm.release_id = r.id "
            "JOIN module_versions mv ON mv.id = rm.module_version_id WHERE p.code = 'e2e-demo-program'"
        )
        == "published|module_versions|published"
    )


# --------------------------------------------------------------------------- catalogue (2.2.2-c)
def _exposable_sql() -> str:
    """Règle d'exposition du catalogue public, lue dans le code de l'API (sans l'importer)."""
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "services" / "api" / "app" / "domains" / "catalog" / "editions.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EXPOSABLE_SQL":
            return str(node.value.value)  # type: ignore[attr-defined]
    raise AssertionError("EXPOSABLE_SQL introuvable")


def test_catalog_exposes_historical_0005_editions_then_the_new_edition(scratch):
    """Compatibilité : une édition manifest_v1 publiée sous 0005 reste exposée après 0006 ;
    une nouvelle édition relationnelle la remplace explicitement."""
    d = scratch("cat", "0005_learning_engine")
    w, ed = World(d), Editorial(d)
    a, r = w.user("instructor"), w.user("medical_reviewer")
    program = w.program(medical=False)
    module, (lesson,) = w.module()
    fr = ed.approved_version(lesson, a, r, "fr", sources=0)
    rel_v1 = ed.release(program, a, ed.manifest((module, [(lesson, {"fr": fr})])))
    ed.publish_release(rel_v1, a, r)
    d.file(UP)

    exposure = f"SELECT release_id || '|' || module_count FROM ({_exposable_sql()}) e WHERE program_id = '{program}'"
    assert d.sql(exposure) == f"{rel_v1}|1"
    assert d.sql(f"SELECT lesson_version_id FROM nl_release_lessons('{rel_v1}') WHERE locale = 'fr'") == fr

    ed2 = EditorialV2(d)
    fr2 = ed.approved_version(lesson, a, r, "fr", sources=0)
    mv = ed2.approved_module_version(module, a, r, [(lesson, {"fr": fr2})])
    rel_v2 = ed2.release_v2(program, a, [(module, mv)])
    ed2.publish_release(rel_v2, a, r)
    assert d.sql(exposure) == f"{rel_v2}|1"
    assert d.sql(f"SELECT lesson_version_id FROM nl_release_lessons('{rel_v2}')") == fr2

"""Migration 0005 (Phase 2.2.1) : éditions, workflow éditorial, inscriptions, historique.

Tous les comportements sont vérifiés sur PostgreSQL réel : les invariants critiques sont
portés par la base, indépendamment de l'API qui viendra au sous-lot 2.2.2.

Depuis 0006 (D12, ADR-0010), les nouvelles éditions citent des versions de modules ; ce module
reste la non-régression des éditions historiques (manifeste JSON) et s'exécute donc sur une base
arrêtée à 0005. Le comportement à jour est vérifié par test_program_versions.py.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import SCHEMA, SEEDS, Db, new_id
from editorial import Editorial, q


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def db(db_0005):  # noqa: F811 — remplace la base « tête » pour ce module
    return db_0005


@pytest.fixture
def ed(db) -> Editorial:
    return Editorial(db)


@pytest.fixture
def author(make_user, grant_role):
    uid = make_user()
    grant_role(uid, "instructor")
    return uid


@pytest.fixture
def reviewer(make_reviewer):
    return make_reviewer()


def _program(db, specialty_id: str, org: str | None = None, medical: bool = True) -> str:
    pid = new_id()
    org_sql = f"'{org}'" if org else "NULL"
    tier = "organization" if org else "free"
    db.sql(
        "INSERT INTO programs (id, code, profession_id, specialty_id, organization_id, title_i18n, level, access_tier, "
        f"is_medical) SELECT '{pid}', 'p-{pid[:8]}', p.id, '{specialty_id}', {org_sql}, "
        f"'{{\"fr\":\"Programme de test\"}}', 'beginner', '{tier}', {str(medical).lower()} "
        "FROM professions p JOIN jurisdictions j ON j.id = p.jurisdiction_id WHERE j.code = 'CA-QC' AND p.code = 'pab'"
    )
    return pid


def _module(db, specialty_id: str, org: str | None = None, lessons: int = 1) -> tuple[str, list[str]]:
    mid = new_id()
    org_sql = f"'{org}'" if org else "NULL"
    db.sql(
        f"INSERT INTO modules (id, code, specialty_id, organization_id, level, title_i18n) VALUES "
        f"('{mid}', 'm-{mid[:8]}', '{specialty_id}', {org_sql}, 'beginner', '{{\"fr\":\"Module de test\"}}')"
    )
    ids = []
    for i in range(lessons):
        lid = new_id()
        db.sql(
            f"INSERT INTO lessons (id, module_id, code, position, kind) VALUES ('{lid}', '{mid}', 'l{i}', {i}, 'theory')"
        )
        ids.append(lid)
    return mid, ids


@pytest.fixture
def course(db, ed, specialty_id, author, reviewer):
    """Programme médical public, un module, une leçon avec versions FR et EN approuvées."""
    program = _program(db, specialty_id)
    module, (lesson,) = _module(db, specialty_id)
    fr = ed.approved_version(lesson, author, reviewer, "fr")
    en = ed.approved_version(lesson, author, reviewer, "en")
    manifest = ed.manifest((module, [(lesson, {"fr": fr, "en": en})]))
    return {"program": program, "module": module, "lesson": lesson, "fr": fr, "en": en, "manifest": manifest}


@pytest.fixture
def published(db, ed, course, author, reviewer):
    release = ed.release(course["program"], author, course["manifest"])
    ed.publish_release(release, author, reviewer)
    return {**course, "release": release}


def _student(make_user, grant_role) -> str:
    uid = make_user()
    grant_role(uid, "student")
    return uid


def _enroll(db, user: str, program: str, release: str, org: str | None = None, cohort: str | None = None) -> str:
    eid = new_id()
    db.sql(
        "INSERT INTO enrollments (id, user_id, program_id, release_id, organization_id, cohort_id) VALUES "
        f"('{eid}', '{user}', '{program}', '{release}', {q(org) if org else 'NULL'}, {q(cohort) if cohort else 'NULL'})"
    )
    return eid


def _event(enrollment: str, user: str, release: str, kind: str, c: dict, block: str | None = None, **kw) -> str:
    cols = {
        "enrollment_id": enrollment,
        "user_id": user,
        "release_id": release,
        "client_event_id": kw.get("client_id", new_id()),
        "event_type": kind,
        "occurred_at": kw.get("occurred_at", "now()"),
    }
    if kind in ("lesson_opened", "block_reached", "lesson_completed", "module_completed"):
        cols["module_id"] = c["module"]
    if kind in ("lesson_opened", "block_reached", "lesson_completed"):
        cols["lesson_id"] = kw.get("lesson", c["lesson"])
        cols["lesson_version_id"] = kw.get("version", c["fr"])
    if block:
        cols["block_id"] = block
    values = ", ".join(v if v == "now()" or v.startswith("now()") else q(v) for v in cols.values())
    return f"INSERT INTO learning_events ({', '.join(cols)}) VALUES ({values})"


# =========================================================================== schéma et compatibilité
def test_new_tables_and_columns_exist(db):
    tables = db.sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
        "WHERE table_name IN ('program_releases','enrollments','learning_events','editorial_decisions')"
    )
    assert tables == "editorial_decisions,enrollments,learning_events,program_releases"
    assert db.sql(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    ) in ("65", "66")  # 66 avec pgvector (0002)
    for col in (
        "lesson_versions.content_sha256",
        "lesson_versions.published_by",
        "lesson_versions.submitted_at",
        "programs.is_medical",
        "student_progress.enrollment_id",
        "student_progress.lesson_version_id",
    ):
        table, name = col.split(".")
        assert (
            db.sql(
                f"SELECT count(*) FROM information_schema.columns WHERE table_name = '{table}' "
                f"AND column_name = '{name}'"
            )
            == "1"
        ), col


def test_in_review_code_is_replaced_by_medical_review(db, ed, make_lesson, author):
    definition = db.sql(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_lesson_versions_status'"
    )
    assert "medical_review" in definition and "in_review" not in definition
    vid = ed.lesson_version(make_lesson(), author)
    assert "interdite" in ed.fails(author, f"UPDATE lesson_versions SET status = 'in_review' WHERE id = '{vid}'")


def test_programs_are_medical_by_default(db, specialty_id):
    pid = new_id()
    db.sql(
        "INSERT INTO programs (id, code, profession_id, title_i18n, level) SELECT "
        f"'{pid}', 'p-{pid[:8]}', id, '{{\"fr\":\"P\"}}', 'beginner' FROM professions WHERE code = 'pab'"
    )
    assert db.sql(f"SELECT is_medical FROM programs WHERE id = '{pid}'") == "t"


# =========================================================================== versions de leçon
def test_version_is_born_draft_and_numbered_continuously(db, ed, make_lesson, author, reviewer):
    lid = make_lesson()
    body = q('{"blocks":[]}')
    msg = db.fails(
        "INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status, author_id, reviewed_by, "
        f"reviewed_at) VALUES ('{lid}', 'fr', 1, 'Leçon', {body}, 'approved', '{author}', '{reviewer}', now())"
    )
    assert "naît en brouillon" in msg
    assert "trace de relecture" in db.fails(
        "INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, reviewed_by) "
        f"VALUES ('{lid}', 'fr', 1, 'Leçon', {body}, '{reviewer}')"
    )
    ed.lesson_version(lid, author)
    assert "attendu : 2" in db.fails(
        f"INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body) VALUES ('{lid}', 'fr', 5, 'L', {body})"
    )


def test_full_workflow_is_logged_with_actors_and_fingerprint(db, ed, make_lesson, author, reviewer):
    vid = ed.lesson_version(make_lesson(), author)
    ed.cite_source(vid)
    ed.submit(vid, author)
    ed.approve(vid, reviewer)
    ed.set_status("lesson_versions", vid, "published", reviewer)
    row = db.sql(
        f"SELECT status, reviewed_by, published_by, content_sha256 IS NOT NULL, submitted_at IS NOT NULL "
        f"FROM lesson_versions WHERE id = '{vid}'"
    )
    assert row == f"published|{reviewer}|{reviewer}|t|t"
    log = db.sql(
        "SELECT string_agg(decision || ':' || actor_id || ':' || (content_sha256 IS NOT NULL)::text, ',' ORDER BY id) "
        f"FROM editorial_decisions WHERE entity_type = 'lesson_version' AND entity_id = '{vid}'"
    )
    assert log == f"submitted:{author}:true,approved:{reviewer}:true,published:{reviewer}:true"


def test_transition_requires_a_declared_actor(db, ed, make_lesson, author):
    vid = ed.lesson_version(make_lesson(), author)
    assert "sans acteur" in db.fails(f"UPDATE lesson_versions SET status = 'medical_review' WHERE id = '{vid}'")


@pytest.mark.parametrize(
    "path",
    [
        ("approved",),
        ("published",),
        ("medical_review", "published"),
        ("medical_review", "approved", "draft"),
        ("medical_review", "approved", "published", "approved"),
        ("medical_review", "approved", "published", "archived", "draft"),
    ],
)
def test_unauthorised_transitions_are_refused(db, ed, make_lesson, author, reviewer, path):
    vid = ed.lesson_version(make_lesson(), author)
    ed.cite_source(vid)
    *allowed, forbidden = path
    for step in allowed:
        actor = author if step == "medical_review" else reviewer
        extra = ", next_review_due = current_date + 365" if step == "approved" else ""
        ed.set_status("lesson_versions", vid, step, actor, extra)
    extra = ", next_review_due = current_date + 365" if forbidden == "approved" else ""
    assert "interdite" in ed.fails(
        reviewer, f"UPDATE lesson_versions SET status = '{forbidden}'{extra} WHERE id = '{vid}'"
    )


def test_author_cannot_approve_own_content_even_with_review_permission(db, ed, make_lesson, author, grant_role):
    grant_role(author, "medical_reviewer")  # l'auteur est aussi relecteur habilité
    vid = ed.lesson_version(make_lesson(), author)
    ed.submit(vid, author)
    assert "Auto-approbation interdite" in ed.fails(
        author,
        f"UPDATE lesson_versions SET status = 'approved', next_review_due = current_date + 365 WHERE id = '{vid}'",
    )


def test_reviewer_must_hold_review_permission_in_scope(db, ed, make_lesson, make_user, grant_role, author, make_org):
    vid = ed.lesson_version(make_lesson(), author)
    ed.submit(vid, author)
    other_instructor, admin_of_org = make_user(), make_user()
    grant_role(other_instructor, "instructor")
    grant_role(admin_of_org, "org_admin", make_org())
    for actor in (other_instructor, admin_of_org, make_user()):
        assert "content:review" in ed.fails(
            actor,
            f"UPDATE lesson_versions SET status = 'approved', next_review_due = current_date + 365 WHERE id = '{vid}'",
        )
    # Seul un titulaire de content:create peut soumettre.
    v2 = ed.lesson_version(make_lesson(), author)
    assert "content:create" in ed.fails(
        make_user(), f"UPDATE lesson_versions SET status = 'medical_review' WHERE id = '{v2}'"
    )


def test_changes_requested_require_a_reason(db, ed, make_lesson, author, reviewer):
    vid = ed.lesson_version(make_lesson(), author)
    ed.submit(vid, author)
    assert "motif obligatoire" in ed.fails(reviewer, f"UPDATE lesson_versions SET status = 'draft' WHERE id = '{vid}'")
    ed.set_status("lesson_versions", vid, "draft", reviewer, comment="Préciser la source de la recommandation")
    assert ed.status("lesson_versions", vid) == "draft"
    assert (
        db.sql(f"SELECT comment FROM editorial_decisions WHERE entity_id = '{vid}' AND decision = 'changes_requested'")
        == "Préciser la source de la recommandation"
    )


def test_submission_needs_author_and_content(db, ed, make_lesson, author):
    no_author = ed.lesson_version(make_lesson(), None)
    assert "auteur non renseigné" in ed.fails(
        author, f"UPDATE lesson_versions SET status = 'medical_review' WHERE id = '{no_author}'"
    )
    empty = ed.lesson_version(make_lesson(), author, blocks=0)
    assert "sans contenu" in ed.fails(
        author, f"UPDATE lesson_versions SET status = 'medical_review' WHERE id = '{empty}'"
    )


def test_approval_needs_a_future_review_date(db, ed, make_lesson, author, reviewer):
    vid = ed.lesson_version(make_lesson(), author)
    ed.submit(vid, author)
    assert "prochaine révision" in ed.fails(
        reviewer, f"UPDATE lesson_versions SET status = 'approved' WHERE id = '{vid}'"
    )
    assert "prochaine révision" in ed.fails(
        reviewer, f"UPDATE lesson_versions SET status = 'approved', next_review_due = current_date WHERE id = '{vid}'"
    )


def test_review_fields_cannot_be_forged(db, ed, make_lesson, author, reviewer):
    vid = ed.lesson_version(make_lesson(), author)
    assert "trace de relecture" in db.fails(f"UPDATE lesson_versions SET reviewed_by = '{reviewer}' WHERE id = '{vid}'")
    assert "relecture" in ed.fails(
        author,
        f"UPDATE lesson_versions SET status = 'medical_review', reviewed_by = '{reviewer}', reviewed_at = now() "
        f"WHERE id = '{vid}'",
    )
    ed.submit(vid, author)
    ed.approve(vid, reviewer)
    assert db.sql(f"SELECT reviewed_by FROM lesson_versions WHERE id = '{vid}'") == reviewer


def test_approved_content_is_immutable_and_changes_need_a_new_version(db, ed, make_lesson, author, reviewer):
    lid = make_lesson()
    vid = ed.approved_version(lid, author, reviewer)
    for change in (
        "title = 'Autre titre'",
        'body = \'{"blocks":[{"id":"x"}]}\'',
        "locale = 'en'",
        "next_review_due = current_date + 10",
        f"author_id = '{reviewer}'",
    ):
        assert "modification directe interdite" in db.fails(f"UPDATE lesson_versions SET {change} WHERE id = '{vid}'")
    # Les sources sont figées, la version n'est pas supprimable.
    assert "non modifiables" in db.fails(f"DELETE FROM lesson_version_references WHERE lesson_version_id = '{vid}'")
    assert "non modifiables" in db.fails(
        f"INSERT INTO lesson_version_references (lesson_version_id, reference_id) "
        f"SELECT '{vid}', id FROM medical_references LIMIT 1"
    )
    assert "non supprimable" in db.fails(f"DELETE FROM lesson_versions WHERE id = '{vid}'")
    # Une modification passe par une nouvelle version, à nouveau soumise à relecture.
    v2 = ed.lesson_version(lid, author)
    assert db.sql(f"SELECT version_no, status FROM lesson_versions WHERE id = '{v2}'") == "2|draft"


def test_publication_detects_tampering_since_approval(db, ed, make_lesson, author, reviewer):
    vid = ed.approved_version(make_lesson(), author, reviewer)
    # Même en contournant le déclencheur (superutilisateur), l'empreinte révèle la modification.
    db.sql(
        f"ALTER TABLE lesson_versions DISABLE TRIGGER trg_lesson_versions_10_workflow; "
        f"UPDATE lesson_versions SET title = 'Altéré' WHERE id = '{vid}'; "
        f"ALTER TABLE lesson_versions ENABLE TRIGGER trg_lesson_versions_10_workflow;"
    )
    assert "a changé depuis l'approbation" in ed.fails(
        reviewer, f"UPDATE lesson_versions SET status = 'published' WHERE id = '{vid}'"
    )


# =========================================================================== journal des décisions
def test_editorial_decisions_are_append_only_and_written_by_transitions_only(db, ed, make_lesson, author, reviewer):
    vid = ed.lesson_version(make_lesson(), author)
    ed.submit(vid, author)
    assert "uniquement par les transitions" in db.fails(
        "INSERT INTO editorial_decisions (entity_type, entity_id, from_status, to_status, decision, actor_id) "
        f"VALUES ('lesson_version', '{vid}', 'medical_review', 'approved', 'approved', '{reviewer}')"
    )
    assert "ajout seul" in db.fails(f"UPDATE editorial_decisions SET comment = 'x' WHERE entity_id = '{vid}'")
    assert "ajout seul" in db.fails(f"DELETE FROM editorial_decisions WHERE entity_id = '{vid}'")
    assert "ajout seul" in db.fails("TRUNCATE editorial_decisions")


# =========================================================================== éditions de programme
def test_release_publication_publishes_program_modules_and_versions(db, ed, published, reviewer):
    c = published
    assert ed.status("program_releases", c["release"]) == "published"
    assert ed.status("programs", c["program"]) == "published"
    assert ed.status("modules", c["module"]) == "published"
    assert ed.status("lesson_versions", c["fr"]) == ed.status("lesson_versions", c["en"]) == "published"
    decisions = db.sql(
        "SELECT string_agg(decision, ',' ORDER BY id) FROM editorial_decisions "
        f"WHERE entity_type = 'program_release' AND entity_id = '{c['release']}'"
    )
    assert decisions == "submitted,approved,published"
    assert db.sql(f"SELECT published_by FROM lesson_versions WHERE id = '{c['fr']}'") == reviewer


def test_published_release_is_immutable(db, ed, published):
    rid = published["release"]
    for change in (
        "manifest = '{\"modules\":[]}'",
        "change_summary = 'x'",
        "release_no = 9",
        f"program_id = '{new_id()}'",
    ):
        assert "non modifiable" in db.fails(
            f"UPDATE program_releases SET {change} WHERE id = '{rid}'"
        ) or "interdite" in db.fails(f"UPDATE program_releases SET {change} WHERE id = '{rid}'")
    assert "non supprimable" in db.fails(f"DELETE FROM program_releases WHERE id = '{rid}'")


def test_release_in_review_is_frozen(db, ed, course, author):
    rid = ed.release(course["program"], author, course["manifest"])
    db.sql(f"UPDATE program_releases SET change_summary = 'Brouillon modifiable' WHERE id = '{rid}'")
    ed.set_status("program_releases", rid, "medical_review", author)
    assert "modification directe interdite" in db.fails(
        f"UPDATE program_releases SET change_summary = 'trop tard' WHERE id = '{rid}'"
    )


def test_programs_and_modules_cannot_be_published_directly(db, specialty_id):
    pid = _program(db, specialty_id)
    assert "édition publiée" in db.fails(f"UPDATE programs SET status = 'published' WHERE id = '{pid}'")
    mid, _ = _module(db, specialty_id)
    assert "édition publiée" in db.fails(f"UPDATE modules SET status = 'published' WHERE id = '{mid}'")
    assert "édition publiée" in db.fails(
        "INSERT INTO programs (code, profession_id, title_i18n, level, status) SELECT "
        f"'p-{new_id()[:8]}', id, '{{\"fr\":\"P\"}}', 'beginner', 'published' FROM professions WHERE code = 'pab'"
    )


def test_medical_release_requires_reviewed_versions(db, ed, specialty_id, author, reviewer):
    program = _program(db, specialty_id)
    module, (lesson,) = _module(db, specialty_id)
    draft = ed.lesson_version(lesson, author)
    rid = ed.release(program, author, ed.manifest((module, [(lesson, {"fr": draft})])))
    assert "encore en « draft »" in ed.fails(
        author, f"UPDATE program_releases SET status = 'medical_review' WHERE id = '{rid}'"
    )
    # Version soumise mais non approuvée : l'édition peut être soumise, pas approuvée.
    ed.cite_source(draft)
    ed.submit(draft, author)
    ed.set_status("program_releases", rid, "medical_review", author)
    assert "non approuvée" in ed.fails(reviewer, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid}'")


def test_medical_release_requires_active_sources_for_every_lesson(db, ed, specialty_id, author, reviewer):
    program = _program(db, specialty_id)
    module, (lesson,) = _module(db, specialty_id)
    no_source = ed.approved_version(lesson, author, reviewer, sources=0)
    rid = ed.release(program, author, ed.manifest((module, [(lesson, {"fr": no_source})])))
    ed.set_status("program_releases", rid, "medical_review", author)
    assert "aucune source active" in ed.fails(
        reviewer, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid}'"
    )
    # Une source retirée ne compte pas.
    module2, (lesson2,) = _module(db, specialty_id)
    withdrawn = ed.lesson_version(lesson2, author)
    ed.cite_source(withdrawn, status="withdrawn")
    ed.submit(withdrawn, author)
    ed.approve(withdrawn, reviewer)
    program2 = _program(db, specialty_id)
    rid2 = ed.release(program2, author, ed.manifest((module2, [(lesson2, {"fr": withdrawn})])))
    ed.set_status("program_releases", rid2, "medical_review", author)
    assert "aucune source active" in ed.fails(
        reviewer, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid2}'"
    )


def test_non_medical_release_does_not_require_sources_but_still_review(db, ed, specialty_id, author, reviewer):
    program = _program(db, specialty_id, medical=False)
    module, (lesson,) = _module(db, specialty_id)
    vid = ed.approved_version(lesson, author, reviewer, sources=0)
    rid = ed.release(program, author, ed.manifest((module, [(lesson, {"fr": vid})])))
    ed.set_status("program_releases", rid, "medical_review", author)
    assert "content:review" in ed.fails(author, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid}'")
    ed.set_status("program_releases", rid, "approved", reviewer)
    assert ed.status("program_releases", rid) == "approved"


def test_release_reviewer_must_be_independent_from_included_authors(db, ed, course, author, make_reviewer, grant_role):
    # Un second relecteur rédige une leçon de l'édition : il ne peut pas approuver l'édition.
    co_author = make_reviewer()
    grant_role(co_author, "instructor")
    module2, (lesson2,) = _module(db, ed.db.sql("SELECT id FROM specialties WHERE code = 'care_assistant_core'"))
    other_reviewer = make_reviewer()
    v = ed.approved_version(lesson2, co_author, other_reviewer)
    data = json.loads(course["manifest"])
    data["modules"].append(
        {
            "module_id": module2,
            "position": 1,
            "required": True,
            "lessons": [{"lesson_id": lesson2, "position": 0, "versions": {"fr": v}}],
        }
    )
    rid = ed.release(course["program"], author, json.dumps(data))
    ed.set_status("program_releases", rid, "medical_review", author)
    assert "Indépendance" in ed.fails(co_author, f"UPDATE program_releases SET status = 'approved' WHERE id = '{rid}'")


def test_incoherent_manifests_are_refused(db, ed, specialty_id, author, reviewer, make_org):
    program = _program(db, specialty_id)
    module, (lesson, lesson_b) = _module(db, specialty_id, lessons=2)
    other_module, (foreign_lesson,) = _module(db, specialty_id)
    fr = ed.approved_version(lesson, author, reviewer, "fr")
    en = ed.approved_version(lesson, author, reviewer, "en")
    fr_b = ed.approved_version(lesson_b, author, reviewer, "fr")
    ontario = db.sql("SELECT id FROM jurisdictions WHERE code = 'CA-ON'")
    on_version = ed.lesson_version(lesson_b, author, jurisdiction=ontario)
    ed.cite_source(on_version)
    ed.submit(on_version, author)
    ed.approve(on_version, reviewer)
    private_module, (private_lesson,) = _module(db, specialty_id, org=make_org())
    private_v = ed.approved_version(private_lesson, author, reviewer)

    cases = {
        "absente du module": ed.manifest((module, [(foreign_lesson, {"fr": fr})])),
        "n'appartient pas": ed.manifest((module, [(lesson, {"fr": fr_b})])),
        "rangée sous": ed.manifest((module, [(lesson, {"fr": en})])),
        "autre juridiction": ed.manifest((module, [(lesson_b, {"fr": on_version})])),
        "sans version française": ed.manifest((module, [(lesson, {"en": en})])),
        "citée deux fois": json.dumps(
            {
                "modules": [
                    {
                        "module_id": module,
                        "position": 0,
                        "required": True,
                        "lessons": [
                            {"lesson_id": lesson, "position": 0, "versions": {"fr": fr}},
                            {"lesson_id": lesson, "position": 1, "versions": {"fr": fr}},
                        ],
                    }
                ]
            }
        ),
        "Module privé": ed.manifest((private_module, [(private_lesson, {"fr": private_v})])),
        "au moins un module": json.dumps({"modules": []}),
        "inconnu": ed.manifest((new_id(), [(lesson, {"fr": fr})])),
    }
    for expected, manifest in cases.items():
        rid = ed.release(program, author, manifest)
        assert expected in ed.fails(
            author, f"UPDATE program_releases SET status = 'medical_review' WHERE id = '{rid}'"
        ), expected
        db.sql(f"DELETE FROM program_releases WHERE id = '{rid}'")  # brouillon : supprimable
    del other_module


def test_release_numbers_are_continuous(db, ed, course, author):
    ed.release(course["program"], author, course["manifest"])
    assert "attendu : 2" in db.fails(
        "INSERT INTO program_releases (program_id, release_no, author_id) "
        f"VALUES ('{course['program']}', 7, '{author}')"
    )


def test_program_nature_is_frozen_once_a_release_is_submitted(db, ed, course, author):
    rid = ed.release(course["program"], author, course["manifest"])
    db.sql(f"UPDATE programs SET is_medical = false WHERE id = '{course['program']}'")  # brouillon seulement : permis
    db.sql(f"UPDATE programs SET is_medical = true WHERE id = '{course['program']}'")
    ed.set_status("program_releases", rid, "medical_review", author)
    assert "figés" in db.fails(f"UPDATE programs SET is_medical = false WHERE id = '{course['program']}'")


def test_new_release_archives_previous_and_supersedes_versions(db, ed, published, author, reviewer):
    c = published
    fr2 = ed.approved_version(c["lesson"], author, reviewer, "fr")
    r2 = ed.release(c["program"], author, ed.manifest((c["module"], [(c["lesson"], {"fr": fr2, "en": c["en"]})])))
    ed.publish_release(r2, author, reviewer)
    assert ed.status("program_releases", c["release"]) == "archived"
    assert ed.status("program_releases", r2) == "published"
    assert ed.status("lesson_versions", c["fr"]) == "archived"  # remplacée
    assert ed.status("lesson_versions", c["en"]) == "published"  # reprise telle quelle
    assert ed.status("lesson_versions", fr2) == "published"
    assert ed.status("programs", c["program"]) == "published"
    assert (
        db.sql(f"SELECT count(*) FROM program_releases WHERE program_id = '{c['program']}' AND status = 'published'")
        == "1"
    )


def test_shared_version_still_used_by_another_published_release_blocks_supersession(
    db, ed, published, specialty_id, author, reviewer
):
    c = published
    other_program = _program(db, specialty_id)
    other = ed.release(other_program, author, c["manifest"])  # même module, mêmes versions
    ed.publish_release(other, author, reviewer)
    fr2 = ed.approved_version(c["lesson"], author, reviewer, "fr")
    r2 = ed.release(c["program"], author, ed.manifest((c["module"], [(c["lesson"], {"fr": fr2, "en": c["en"]})])))
    ed.set_status("program_releases", r2, "medical_review", author)
    ed.set_status("program_releases", r2, "approved", reviewer)
    assert "encore suivie par une autre édition" in ed.fails(
        reviewer, f"UPDATE program_releases SET status = 'published' WHERE id = '{r2}'"
    )


# =========================================================================== inscriptions
def test_enrollment_only_to_published_release_of_same_program(
    db, ed, course, published, make_user, grant_role, specialty_id, author
):
    student = _student(make_user, grant_role)
    draft = ed.release(course["program"], author, course["manifest"])
    assert "édition publiée" in db.fails(
        f"INSERT INTO enrollments (user_id, program_id, release_id) VALUES ('{student}', '{course['program']}', '{draft}')"
    )
    other_program = _program(db, specialty_id)
    assert (
        "foreign key"
        in db.fails(
            f"INSERT INTO enrollments (user_id, program_id, release_id) VALUES "
            f"('{student}', '{other_program}', '{published['release']}')"
        ).lower()
    )
    eid = _enroll(db, student, course["program"], published["release"])
    assert "duplicate key" in db.fails(
        f"INSERT INTO enrollments (user_id, program_id, release_id) VALUES "
        f"('{student}', '{course['program']}', '{published['release']}')"
    )
    assert db.sql(f"SELECT status FROM enrollments WHERE id = '{eid}'") == "active"


def test_inactive_accounts_cannot_enroll(db, published, make_user):
    uid = make_user()
    db.sql(f"UPDATE users SET status = 'suspended' WHERE id = '{uid}'")
    assert "compte actif" in db.fails(
        f"INSERT INTO enrollments (user_id, program_id, release_id) VALUES "
        f"('{uid}', '{published['program']}', '{published['release']}')"
    )


def test_enrollment_history_is_preserved_across_releases(db, ed, published, make_user, grant_role, author, reviewer):
    c = published
    student = _student(make_user, grant_role)
    eid = _enroll(db, student, c["program"], c["release"])
    db.sql(_event(eid, student, c["release"], "lesson_opened", c))
    fr2 = ed.approved_version(c["lesson"], author, reviewer, "fr")
    r2 = ed.release(c["program"], author, ed.manifest((c["module"], [(c["lesson"], {"fr": fr2, "en": c["en"]})])))
    ed.publish_release(r2, author, reviewer)
    # L'inscription reste sur l'édition suivie (archivée) ; son historique est intact.
    assert db.sql(f"SELECT release_id FROM enrollments WHERE id = '{eid}'") == c["release"]
    assert db.sql(f"SELECT count(*) FROM learning_events WHERE enrollment_id = '{eid}'") == "1"
    # Bascule vers l'édition plus récente, jamais en arrière.
    db.sql(f"UPDATE enrollments SET release_id = '{r2}' WHERE id = '{eid}'")
    assert "plus récente" in db.fails(f"UPDATE enrollments SET release_id = '{c['release']}' WHERE id = '{eid}'")
    assert "non supprimable" in db.fails(f"DELETE FROM enrollments WHERE id = '{eid}'")
    assert "non modifiables" in db.fails(f"UPDATE enrollments SET user_id = '{author}' WHERE id = '{eid}'")


def test_enrollment_status_transitions(db, published, make_user, grant_role):
    student = _student(make_user, grant_role)
    eid = _enroll(db, student, published["program"], published["release"])
    assert "check" in db.fails(f"UPDATE enrollments SET status = 'completed' WHERE id = '{eid}'").lower()
    db.sql(f"UPDATE enrollments SET status = 'withdrawn', withdrawn_at = now() WHERE id = '{eid}'")
    db.sql(f"UPDATE enrollments SET status = 'active' WHERE id = '{eid}'")  # réinscription
    assert db.sql(f"SELECT withdrawn_at IS NULL FROM enrollments WHERE id = '{eid}'") == "t"
    db.sql(f"UPDATE enrollments SET status = 'completed', completed_at = now() WHERE id = '{eid}'")
    assert "interdite" in db.fails(f"UPDATE enrollments SET status = 'active', completed_at = NULL WHERE id = '{eid}'")


def test_enrollment_organization_isolation(db, ed, specialty_id, author, reviewer, make_org, make_user, grant_role):
    org_a, org_b = make_org(), make_org()
    program = _program(db, specialty_id, org=org_a)
    module, (lesson,) = _module(db, specialty_id, org=org_a)
    fr = ed.approved_version(lesson, author, reviewer)
    rid = ed.release(program, author, ed.manifest((module, [(lesson, {"fr": fr})])))
    ed.publish_release(rid, author, reviewer)

    outsider, member_b, member_a = make_user(), make_user(), make_user()
    grant_role(member_b, "instructor", org_b)
    grant_role(member_a, "instructor", org_a)
    sql = "INSERT INTO enrollments (user_id, program_id, release_id, organization_id) VALUES ('{u}', '{p}', '{r}', {o})"
    assert "cet établissement" in db.fails(sql.format(u=outsider, p=program, r=rid, o="NULL"))
    assert "cet établissement" in db.fails(sql.format(u=member_b, p=program, r=rid, o=f"'{org_b}'"))
    assert "n'appartient pas" in db.fails(sql.format(u=outsider, p=program, r=rid, o=f"'{org_a}'"))
    db.sql(sql.format(u=member_a, p=program, r=rid, o=f"'{org_a}'"))
    # Cohorte d'un autre établissement refusée.
    cohort_b = new_id()
    db.sql(f"INSERT INTO cohorts (id, organization_id, name) VALUES ('{cohort_b}', '{org_b}', 'Groupe B')")
    db.sql(f"INSERT INTO cohort_members (cohort_id, user_id) VALUES ('{cohort_b}', '{member_b}')")
    public = _program(db, specialty_id)
    pmod, (plesson,) = _module(db, specialty_id)
    pv = ed.approved_version(plesson, author, reviewer)
    prid = ed.release(public, author, ed.manifest((pmod, [(plesson, {"fr": pv})])))
    ed.publish_release(prid, author, reviewer)
    assert "Cohorte" in db.fails(
        "INSERT INTO enrollments (user_id, program_id, release_id, organization_id, cohort_id) VALUES "
        f"('{member_a}', '{public}', '{prid}', '{org_a}', '{cohort_b}')"
    )
    db.sql(
        "INSERT INTO enrollments (user_id, program_id, release_id, organization_id, cohort_id) VALUES "
        f"('{member_b}', '{public}', '{prid}', '{org_b}', '{cohort_b}')"
    )


# =========================================================================== historique et idempotence
def test_learning_events_are_idempotent(db, published, make_user, grant_role):
    c = published
    student = _student(make_user, grant_role)
    eid = _enroll(db, student, c["program"], c["release"])
    client_id = new_id()
    event = _event(eid, student, c["release"], "block_reached", c, block="b1", client_id=client_id)
    for _ in range(3):
        db.sql(event + " ON CONFLICT (enrollment_id, client_event_id) DO NOTHING")
    assert db.sql(f"SELECT count(*) FROM learning_events WHERE enrollment_id = '{eid}'") == "1"
    assert "duplicate key" in db.fails(event)


def test_incoherent_events_are_refused(db, ed, published, make_user, grant_role, author, reviewer, specialty_id):
    c = published
    student, other = _student(make_user, grant_role), _student(make_user, grant_role)
    eid = _enroll(db, student, c["program"], c["release"])
    foreign_module, (foreign_lesson,) = _module(db, specialty_id)
    foreign_v = ed.lesson_version(foreign_lesson, author)
    unpublished_v = ed.lesson_version(c["lesson"], author)
    cases = {
        "bloc b99 absent": _event(eid, student, c["release"], "block_reached", c, block="b99"),
        "hors de l'édition suivie": _event(eid, student, c["release"], "lesson_opened", c, version=unpublished_v),
        "leçon ou version hors": _event(
            eid, student, c["release"], "lesson_opened", c, lesson=foreign_lesson, version=foreign_v
        ),
        "foreign key": _event(eid, other, c["release"], "lesson_opened", c),  # titulaire différent
        "check": _event(eid, student, c["release"], "lesson_opened", c, occurred_at="now() + interval '1 day'"),
        "antérieur": _event(eid, student, c["release"], "lesson_opened", c, occurred_at="now() - interval '2 days'"),
    }
    for expected, sql in cases.items():
        assert expected in db.fails(sql).lower() or expected in db.fails(sql), expected
    db.sql(f"UPDATE enrollments SET status = 'withdrawn', withdrawn_at = now() WHERE id = '{eid}'")
    assert "inscription « withdrawn »" in db.fails(_event(eid, student, c["release"], "lesson_opened", c))
    del foreign_module


def test_learning_events_are_append_only(db, published, make_user, grant_role):
    c = published
    student = _student(make_user, grant_role)
    eid = _enroll(db, student, c["program"], c["release"])
    db.sql(_event(eid, student, c["release"], "lesson_opened", c))
    assert "ajout seul" in db.fails(f"UPDATE learning_events SET block_id = 'b0' WHERE enrollment_id = '{eid}'")
    assert "ajout seul" in db.fails(f"DELETE FROM learning_events WHERE enrollment_id = '{eid}'")
    assert "ajout seul" in db.fails("TRUNCATE learning_events")


def test_progress_and_resume_point_stay_within_the_followed_release(
    db, ed, published, make_user, grant_role, make_lesson, author
):
    c = published
    student = _student(make_user, grant_role)
    eid = _enroll(db, student, c["program"], c["release"])
    db.sql(f"UPDATE enrollments SET last_lesson_id = '{c['lesson']}', last_block_id = 'b1' WHERE id = '{eid}'")
    ins = (
        "INSERT INTO student_progress (user_id, lesson_id, enrollment_id, lesson_version_id, last_block_id, "
        "percent_complete) VALUES ('{u}', '{l}', '{e}', '{v}', {b}, 50)"
    )
    db.sql(ins.format(u=student, l=c["lesson"], e=eid, v=c["fr"], b="'b1'"))
    assert "bloc absent" in db.fails(f"UPDATE student_progress SET last_block_id = 'zz' WHERE enrollment_id = '{eid}'")
    stray_lesson = make_lesson()
    stray_v = ed.lesson_version(stray_lesson, author)
    other = _student(make_user, grant_role)
    e2 = _enroll(db, other, c["program"], c["release"])
    assert "hors des éditions" in db.fails(ins.format(u=other, l=stray_lesson, e=e2, v=stray_v, b="NULL"))
    assert "autre leçon" in db.fails(ins.format(u=other, l=c["lesson"], e=e2, v=stray_v, b="NULL"))
    third = _student(make_user, grant_role)  # progression rattachée à l'inscription d'un autre étudiant
    assert "foreign key" in db.fails(ins.format(u=third, l=c["lesson"], e=e2, v=c["fr"], b="NULL")).lower()
    assert "hors de l'édition suivie" in db.fails(
        f"UPDATE enrollments SET last_lesson_id = '{stray_lesson}' WHERE id = '{eid}'"
    )


# =========================================================================== pilote PAB et seeds
def test_pilot_remains_draft_and_seeds_stay_idempotent(db):
    db.file(SEEDS / "0002_pilot_pab_structure.sql")
    assert db.sql("SELECT status FROM programs WHERE code = 'pab-qc-socle'") == "draft"
    assert (
        db.sql(
            "SELECT count(*) FROM program_releases r JOIN programs p ON p.id = r.program_id "
            "WHERE p.code = 'pab-qc-socle'"
        )
        == "0"
    )
    assert (
        db.sql(
            "SELECT count(*) FROM lesson_versions lv JOIN lessons l ON l.id = lv.lesson_id "
            "JOIN modules m ON m.id = l.module_id WHERE m.code = 'hygiene-mains-prevention-infections' "
            "AND lv.status <> 'draft'"
        )
        == "0"
    )


# =========================================================================== migration
def _signature(d: Db) -> str:
    return d.sql(
        "SELECT md5(string_agg(x, '|' ORDER BY x)) FROM ("
        " SELECT 't:' || table_name || '.' || column_name || ':' || data_type || ':' || is_nullable AS x "
        "   FROM information_schema.columns WHERE table_schema = 'public'"
        " UNION ALL SELECT 'c:' || conrelid::regclass || ':' || pg_get_constraintdef(oid) FROM pg_constraint "
        "   WHERE connamespace = 'public'::regnamespace"
        " UNION ALL SELECT 'g:' || tgrelid::regclass || ':' || tgname FROM pg_trigger WHERE NOT tgisinternal"
        " UNION ALL SELECT 'f:' || proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
        " UNION ALL SELECT 'i:' || indexname || ':' || indexdef FROM pg_indexes WHERE schemaname = 'public') s"
    )


def test_migration_up_down_up_restores_schema_exactly(db):
    scratch = Db(f"nl_mig0005_{os.getpid()}")
    scratch.admin(f"DROP DATABASE IF EXISTS {scratch.name}")
    scratch.admin(f"CREATE DATABASE {scratch.name}")
    try:
        scratch.file(SCHEMA / "0001_core.sql")
        if scratch.sql("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'") == "1":
            scratch.file(SCHEMA / "0002_pgvector.sql")
        scratch.file(SCHEMA / "0003_business_rules.sql")
        scratch.file(SCHEMA / "0004_identity.sql")
        for seed in sorted(SEEDS.glob("*.sql")):
            scratch.file(seed)
        before = _signature(scratch)
        scratch.file(SCHEMA / "0005_learning_engine.sql")
        after_up = _signature(scratch)
        scratch.file(SCHEMA / "0005_learning_engine_down.sql")
        assert _signature(scratch) == before  # retour arrière exact
        scratch.file(SCHEMA / "0005_learning_engine.sql")
        assert _signature(scratch) == after_up  # remontée identique
        # Les données de référence et le pilote ont traversé l'aller-retour intacts.
        assert scratch.sql("SELECT status FROM programs WHERE code = 'pab-qc-socle'") == "draft"
        assert scratch.sql("SELECT count(*) FROM lesson_versions") == "8"
    finally:
        scratch.admin(f"DROP DATABASE IF EXISTS {scratch.name} WITH (FORCE)")

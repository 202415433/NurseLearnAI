"""Règles métier renforcées en Phase 1.1 (migration 0003) et contrôle d'accès par rôle."""

from __future__ import annotations

from conftest import new_id
from editorial import Editorial

LV = '{"blocks":[]}'


def _lesson_version(db, lesson_id: str, author: str, reviewer: str) -> str:
    return (
        f"INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status, author_id, reviewed_by, "
        f"reviewed_at) VALUES ('{lesson_id}', 'fr', {abs(hash(reviewer)) % 10000 + 1}, 'Leçon', '{LV}', "
        f"'approved', '{author}', '{reviewer}', now())"
    )


def _competency(db) -> str:
    cid = new_id()
    db.sql(
        f"INSERT INTO competencies (id, code, specialty_id, title_i18n, domain) SELECT '{cid}', 'c-{cid[:8]}', id, "
        f"'{{\"fr\":\"Hygiène des mains\"}}', 'skill' FROM specialties WHERE code = 'care_assistant_core'"
    )
    return cid


# --------------------------------------------------------------------------- RBAC
def _perms(db, role: str) -> set[str]:
    out = db.sql(
        f"SELECT permission_code FROM role_permissions rp JOIN roles r ON r.id = rp.role_id WHERE r.code = '{role}'"
    )
    return set(out.splitlines())


def test_role_permission_matrix(db):
    all_perms = set(db.sql("SELECT code FROM permissions").splitlines())
    assert _perms(db, "admin") == all_perms
    student = _perms(db, "student")
    assert student == {"catalog:read", "learning:participate", "tutor:use"}
    instructor = _perms(db, "instructor")
    assert "competency:verify" in instructor and "content:create" in instructor
    assert not instructor & {"content:review", "content:publish", "user:manage", "billing:manage"}
    reviewer = _perms(db, "medical_reviewer")
    assert {"content:review", "content:publish"} <= reviewer
    assert not reviewer & {"content:create", "user:manage", "competency:verify"}  # séparation auteur / relecteur
    org_admin = _perms(db, "org_admin")
    assert "org:manage" in org_admin and not org_admin & {"content:review", "user:manage", "billing:manage"}


def test_permission_function_respects_org_scope_and_account_status(db, make_user, make_org, grant_role):
    org_a, org_b = make_org(), make_org()
    uid = make_user()
    grant_role(uid, "instructor", org_a)
    has = lambda org: db.sql(f"SELECT nl_user_has_permission('{uid}', 'competency:verify', {org})")  # noqa: E731
    assert has("NULL") == "f"  # rôle d'établissement ≠ rôle global
    assert has(f"'{org_a}'") == "t"
    assert has(f"'{org_b}'") == "f"
    db.sql(f"UPDATE users SET status = 'suspended' WHERE id = '{uid}'")
    assert has(f"'{org_a}'") == "f"  # compte suspendu : plus aucun droit


# --------------------------------------------------------------------------- relecture indépendante et habilitée
def test_lesson_reviewer_must_hold_review_permission(db, make_lesson, make_user, make_reviewer, grant_role):
    # Depuis 0005, une version naît en brouillon et l'approbation est une transition déclarée par son acteur.
    ed = Editorial(db)
    author = make_user()
    grant_role(author, "instructor")
    vid = ed.lesson_version(make_lesson(), author)
    ed.submit(vid, author)
    approve = f"UPDATE lesson_versions SET status = 'approved', next_review_due = current_date + 365 WHERE id = '{vid}'"
    assert "content:review" in ed.fails(make_user(), approve)
    instructor = make_user()
    grant_role(instructor, "instructor")
    assert "content:review" in ed.fails(instructor, approve)
    ed.run(make_reviewer(), approve)
    # Écriture directe de reviewed_by : refusée (seule la transition d'approbation le renseigne).
    v2 = ed.lesson_version(make_lesson(), author)
    assert "trace de relecture" in db.fails(
        f"UPDATE lesson_versions SET reviewed_by = '{make_user()}' WHERE id = '{v2}'"
    )


def test_reviewer_cannot_review_own_content_even_if_habilitated(db, make_lesson, make_reviewer, grant_role):
    ed = Editorial(db)
    reviewer = make_reviewer()
    grant_role(reviewer, "instructor")
    vid = ed.lesson_version(make_lesson(), reviewer)
    ed.submit(vid, reviewer)
    assert "Auto-approbation interdite" in ed.fails(
        reviewer,
        f"UPDATE lesson_versions SET status = 'approved', next_review_due = current_date + 365 WHERE id = '{vid}'",
    )


def test_question_and_simulation_reviewers_are_checked(db, make_user, make_reviewer, specialty_id):
    outsider = make_user()
    q = (
        f"INSERT INTO questions (type, specialty_id, level, difficulty, stem_i18n, explanation_i18n, status, reviewed_by, "
        f"reviewed_at) VALUES ('true_false', '{specialty_id}', 'beginner', 1, '{{\"fr\":\"?\"}}', '{{\"fr\":\"!\"}}', "
        f"'approved', '{{r}}', now())"
    )
    assert "non habilité" in db.fails(q.replace("{r}", outsider))
    db.sql(q.replace("{r}", make_reviewer()))
    s = (
        f"INSERT INTO clinical_simulations (code, specialty_id, level, title_i18n, virtual_patient, scenario, max_score, "
        f"status, reviewed_by, reviewed_at) VALUES ('s-{new_id()[:8]}', '{specialty_id}', 'beginner', '{{\"fr\":\"S\"}}', "
        f"'{{}}', '{{\"start\":\"a\",\"nodes\":{{}}}}', 10, 'approved', '{outsider}', now())"
    )
    assert "non habilité" in db.fails(s)


def test_profession_and_ai_source_validation_require_habilitation(db, make_user, make_reviewer):
    outsider, reviewer = make_user(), make_reviewer()
    upd = (
        "UPDATE professions SET validation_status='validated', validated_by='{u}', validated_at=now() WHERE code='rpn'"
    )
    assert "non habilité" in db.fails(upd.format(u=outsider))
    db.sql(upd.format(u=reviewer))
    rid = db.sql("SELECT id FROM medical_references LIMIT 1") or db.sql(
        "INSERT INTO medical_references (kind, title, url) VALUES ('guideline','OMS','https://www.who.int/') RETURNING id"
    )
    rid = rid.splitlines()[0]
    src = (
        f"INSERT INTO ai_knowledge_sources (reference_id, locale, storage_key, sha256, license, status, approved_by, "
        f"approved_at) VALUES ('{rid}', 'fr', 'k', '{{h}}', 'CC-BY', 'approved', '{{u}}', now())"
    )
    assert "non habilité" in db.fails(src.replace("{h}", "b" * 64).replace("{u}", outsider))
    db.sql(src.replace("{h}", "c" * 64).replace("{u}", reviewer))


def test_drafts_never_need_a_reviewer(db, make_lesson, make_user):
    # Pilote PAB : sans relecteur confirmé, tout reste en brouillon — et c'est autorisé.
    lid, author = make_lesson(), make_user()
    db.sql(
        f"INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, author_id) "
        f"VALUES ('{lid}', 'fr', 1, 'Hygiène des mains', '{LV}', '{author}')"
    )
    assert db.sql(f"SELECT status FROM lesson_versions WHERE lesson_id = '{lid}'") == "draft"


# --------------------------------------------------------------------------- validation pratique
def test_practice_validation_reserved_to_authorized_instructors(db, make_user, make_org, grant_role):
    org_a, org_b = make_org(), make_org()
    student, cid = make_user(), _competency(db)
    grant_role(student, "student", org_a)
    ins = (
        "INSERT INTO competency_assessments (user_id, competency_id, level, verified_by, verified_at) "
        f"VALUES ('{student}', '{cid}', 'verified_in_practice', '{{v}}', now()) "
        "ON CONFLICT (user_id, competency_id) DO UPDATE SET verified_by = EXCLUDED.verified_by"
    )
    reviewer = make_user()
    grant_role(reviewer, "medical_reviewer")
    assert "non habilité" in db.fails(ins.replace("{v}", reviewer))  # relecteur ≠ formateur
    foreign = make_user()
    grant_role(foreign, "instructor", org_b)
    assert "non habilité" in db.fails(ins.replace("{v}", foreign))  # formateur d'un autre établissement
    local = make_user()
    grant_role(local, "instructor", org_a)
    db.sql(ins.replace("{v}", local))  # formateur de l'établissement : OK


def test_cohort_membership_also_grants_scope(db, make_user, make_org, grant_role):
    org = make_org()
    teacher = make_user()
    grant_role(teacher, "instructor", org)
    db.sql(f"INSERT INTO instructors (user_id, organization_id) VALUES ('{teacher}', '{org}')")
    cohort = new_id()
    db.sql(
        f"INSERT INTO cohorts (id, organization_id, name, instructor_id) VALUES ('{cohort}', '{org}', 'PAB-A', '{teacher}')"
    )
    student, cid = make_user(), _competency(db)
    db.sql(f"INSERT INTO cohort_members (cohort_id, user_id) VALUES ('{cohort}', '{student}')")
    db.sql(
        f"INSERT INTO competency_assessments (user_id, competency_id, level, verified_by, verified_at) "
        f"VALUES ('{student}', '{cid}', 'verified_in_practice', '{teacher}', now())"
    )


# --------------------------------------------------------------------------- isolation par établissement
def test_cohort_instructor_must_belong_to_organization(db, make_user, make_org):
    org_a, org_b = make_org(), make_org()
    teacher = make_user()
    db.sql(f"INSERT INTO instructors (user_id, organization_id) VALUES ('{teacher}', '{org_b}')")
    assert "n'appartient pas" in db.fails(
        f"INSERT INTO cohorts (organization_id, name, instructor_id) VALUES ('{org_a}', 'X', '{teacher}')"
    )


def test_private_module_cannot_leak_into_other_programs(db, make_org, specialty_id):
    org_a, org_b = make_org(), make_org()
    prof = db.sql("SELECT id FROM professions WHERE code = 'pab'").splitlines()[0]
    mid = new_id()
    db.sql(
        f"INSERT INTO modules (id, code, specialty_id, organization_id, level, title_i18n) VALUES "
        f"('{mid}', 'priv-{mid[:8]}', '{specialty_id}', '{org_a}', 'beginner', '{{\"fr\":\"Privé A\"}}')"
    )
    progs = {}
    for name, org in (("public", "NULL"), ("b", f"'{org_b}'"), ("a", f"'{org_a}'")):
        pid = new_id()
        db.sql(
            f"INSERT INTO programs (id, code, profession_id, organization_id, title_i18n, level) VALUES "
            f"('{pid}', 'p-{pid[:8]}', '{prof}', {org}, '{{\"fr\":\"P\"}}', 'beginner')"
        )
        progs[name] = pid
    link = "INSERT INTO program_modules (program_id, module_id, position) VALUES ('{p}', '" + mid + "', 0)"
    assert "hors de l'établissement" in db.fails(link.format(p=progs["public"]))
    assert "hors de l'établissement" in db.fails(link.format(p=progs["b"]))
    db.sql(link.format(p=progs["a"]))


def test_private_question_cannot_be_used_by_other_organization(db, make_org, specialty_id):
    org_a, org_b = make_org(), make_org()
    qid = new_id()
    db.sql(
        f"INSERT INTO questions (id, type, specialty_id, organization_id, level, difficulty, stem_i18n, explanation_i18n) "
        f"VALUES ('{qid}', 'true_false', '{specialty_id}', '{org_a}', 'beginner', 1, '{{\"fr\":\"?\"}}', '{{\"fr\":\"!\"}}')"
    )
    exams = {}
    for name, org in (("public", "NULL"), ("b", f"'{org_b}'"), ("a", f"'{org_a}'")):
        eid = new_id()
        db.sql(
            f"INSERT INTO exams (id, code, kind, organization_id, title_i18n) VALUES "
            f"('{eid}', 'e-{eid[:8]}', 'mock', {org}, '{{\"fr\":\"E\"}}')"
        )
        exams[name] = eid
    q = "INSERT INTO exam_questions (exam_id, question_id, position) VALUES ('{e}', '" + qid + "', 0)"
    assert "hors de son établissement" in db.fails(q.format(e=exams["public"]))
    assert "hors de son établissement" in db.fails(q.format(e=exams["b"]))
    db.sql(q.format(e=exams["a"]))

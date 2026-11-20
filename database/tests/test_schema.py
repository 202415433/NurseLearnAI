"""Tests du modèle de données NurseLearn AI (Phase 1).

Ces tests vérifient sur un vrai PostgreSQL que les exigences du cahier des
charges sont garanties par la base elle-même, et non seulement par l'API.
"""

from __future__ import annotations

import re

from conftest import SCHEMA, SEEDS, Db, new_id
from editorial import Editorial

# Entités exigées au §14 -> table(s) correspondantes.
REQUIRED_ENTITIES = {
    "Users": "users",
    "Roles": "roles",
    "Profiles": "profiles",
    "Countries": "countries",
    "Jurisdictions": "jurisdictions",
    "Professions": "professions",
    "Specialties": "specialties",
    "Programs": "programs",
    "Modules": "modules",
    "Lessons": "lessons",
    "LearningObjectives": "learning_objectives",
    "Competencies": "competencies",
    "LearningResources": "learning_resources",
    "MedicalReferences": "medical_references",
    "Quizzes": "quizzes",
    "Questions": "questions",
    "Answers": "answers",
    "Exams": "exams",
    "ExamAttempts": "exam_attempts",
    "ClinicalSimulations": "clinical_simulations",
    "SimulationAttempts": "simulation_attempts",
    "StudentProgress": "student_progress",
    "StudyPlans": "study_plans",
    "AIConversations": "ai_conversations",
    "AIKnowledgeSources": "ai_knowledge_sources",
    "Certificates": "certificates",
    "Subscriptions": "subscriptions",
    "Payments": "payments",
    "Organizations": "organizations",
    "Instructors": "instructors",
    "AuditLogs": "audit_logs",
}

LV_BODY = '{"blocks":[]}'


def tables(db: Db) -> set[str]:
    out = db.sql(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    return set(out.splitlines())


# --------------------------------------------------------------------------- structure
def test_all_required_entities_exist(db):
    missing = {e: t for e, t in REQUIRED_ENTITIES.items() if t not in tables(db)}
    assert not missing, f"Entités manquantes : {missing}"


def test_every_foreign_key_has_explicit_delete_rule(db):
    # Toute FK doit avoir une stratégie choisie (pas le "NO ACTION" par défaut).
    out = db.sql(
        "SELECT tc.table_name || '.' || kcu.column_name FROM information_schema.referential_constraints rc "
        "JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name "
        "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name "
        "WHERE rc.delete_rule = 'NO ACTION'"
    )
    assert out == "", f"FK sans stratégie de suppression explicite : {out}"


def test_updated_at_trigger_on_every_table_with_column(db):
    out = db.sql("""
      SELECT c.table_name FROM information_schema.columns c
      JOIN information_schema.tables t USING (table_schema, table_name)
      WHERE c.table_schema='public' AND c.column_name='updated_at' AND t.table_type='BASE TABLE'
        AND NOT EXISTS (SELECT 1 FROM pg_trigger tg JOIN pg_class cl ON cl.oid = tg.tgrelid
                        WHERE cl.relname = c.table_name AND tg.tgname LIKE '%updated_at')""")
    assert out == ""


def test_seeds_are_idempotent(db):
    before = db.sql("SELECT count(*) FROM professions") + "/" + db.sql("SELECT count(*) FROM specialties")
    db.file(SEEDS / "0001_reference_data.sql")
    after = db.sql("SELECT count(*) FROM professions") + "/" + db.sql("SELECT count(*) FROM specialties")
    assert before == after
    assert db.sql("SELECT count(*) FROM specialties WHERE catalog_group = 'H'") == "20"


def test_down_then_up_migration(db):
    scratch = Db(db.name + "_updown")
    scratch.admin(f"DROP DATABASE IF EXISTS {scratch.name}")
    scratch.admin(f"CREATE DATABASE {scratch.name}")
    try:
        scratch.file(SCHEMA / "0001_core.sql")
        n = len(tables(scratch))
        scratch.file(SCHEMA / "0001_core_down.sql")
        assert tables(scratch) == {""} or tables(scratch) == set()
        scratch.file(SCHEMA / "0001_core.sql")
        assert len(tables(scratch)) == n
    finally:
        scratch.admin(f"DROP DATABASE IF EXISTS {scratch.name} WITH (FORCE)")


# --------------------------------------------------------------------------- identité
def test_email_unique_case_insensitive_among_live_accounts(db, make_user):
    uid = make_user("Marie.Tremblay@example.test")
    assert "duplicate key" in db.fails("INSERT INTO users (email) VALUES ('marie.tremblay@EXAMPLE.test')")
    # Après suppression logique, l'adresse peut être réutilisée.
    db.sql(f"UPDATE users SET deleted_at = now(), status='deactivated' WHERE id = '{uid}'")
    db.sql("INSERT INTO users (email) VALUES ('marie.tremblay@example.test')")


def test_active_user_requires_verified_email(db):
    assert "check" in db.fails("INSERT INTO users (email, status) VALUES ('x1@example.test', 'active')").lower()


def test_i18n_text_requires_base_locale(db):
    assert "check" in db.fails("INSERT INTO countries (code, name_i18n) VALUES ('BE', '{\"de\":\"Belgien\"}')").lower()


# --------------------------------------------------------------------------- juridictions
def test_professions_are_scoped_per_jurisdiction(db):
    # Même catégorie, juridictions différentes : deux métiers distincts, jamais fusionnés.
    out = db.sql("SELECT count(DISTINCT p.id) FROM professions p WHERE p.category = 'practical_nurse'")
    assert int(out) >= 2
    jid = db.sql("SELECT id FROM jurisdictions WHERE code='FR'")
    db.sql(
        f"INSERT INTO professions (jurisdiction_id, code, title_i18n, category, is_regulated) "
        f"VALUES ('{jid}', 'pab', '{{\"fr\":\"Test\"}}', 'care_assistant', false)"
    )  # même code, autre juridiction : OK


def test_profession_cannot_be_validated_without_validator(db):
    assert "check" in db.fails("UPDATE professions SET validation_status = 'validated' WHERE code = 'pab'").lower()


# --------------------------------------------------------------------------- qualité médicale
def test_medical_reference_must_be_verifiable(db):
    assert (
        "check"
        in db.fails("INSERT INTO medical_references (kind, title) VALUES ('textbook', 'Sans identifiant')").lower()
    )
    db.sql(
        "INSERT INTO medical_references (kind, title, publisher, url) VALUES "
        "('guideline', 'Hygiène des mains', 'OMS', 'https://www.who.int/')"
    )


def test_lesson_cannot_be_published_without_review(db, make_lesson, make_user, grant_role):
    lid = make_lesson()
    # Publication directe à la création : refusée (0001 : relecteur requis ; 0005 : naissance en brouillon).
    assert "brouillon" in db.fails(
        f"INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status, published_at, next_review_due) "
        f"VALUES ('{lid}', 'fr', 1, 'Leçon', '{LV_BODY}', 'published', now(), current_date + 365)"
    )
    # Publication d'un brouillon ou d'une version en relecture : transition interdite.
    ed = Editorial(db)
    author = make_user()
    grant_role(author, "instructor")
    vid = ed.lesson_version(lid, author)
    assert "interdite" in ed.fails(author, f"UPDATE lesson_versions SET status = 'published' WHERE id = '{vid}'")
    ed.submit(vid, author)
    assert "interdite" in ed.fails(author, f"UPDATE lesson_versions SET status = 'published' WHERE id = '{vid}'")


def test_author_cannot_review_own_lesson(db, make_lesson, make_user):
    lid, author = make_lesson(), make_user()
    # Contrainte d'origine (0001) toujours présente…
    assert "reviewed_by <> author_id" in db.sql(
        "SELECT string_agg(pg_get_constraintdef(oid), ' ') FROM pg_constraint WHERE conrelid = 'lesson_versions'::regclass"
    )
    # … et toute tentative de création déjà approuvée est refusée.
    assert "brouillon" in db.fails(
        f"INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status, author_id, reviewed_by, reviewed_at) "
        f"VALUES ('{lid}', 'fr', 1, 'Leçon', '{LV_BODY}', 'approved', '{author}', '{author}', now())"
    )


def test_single_published_version_per_lesson_locale(db, make_lesson, make_user, make_reviewer, grant_role):
    lid, author, reviewer = make_lesson(), make_user(), make_reviewer()
    grant_role(author, "instructor")
    ed = Editorial(db)
    v1 = ed.approved_version(lid, author, reviewer, "fr")
    en = ed.approved_version(lid, author, reviewer, "en")
    v2 = ed.approved_version(lid, author, reviewer, "fr")
    publish = "UPDATE lesson_versions SET status = 'published' WHERE id = '{v}'"
    ed.run(reviewer, publish.format(v=v1))
    ed.run(reviewer, publish.format(v=en))  # autre langue : OK
    assert "duplicate key" in ed.fails(reviewer, publish.format(v=v2))


def test_images_require_alt_text_and_videos_captions(db, make_lesson):
    lid = make_lesson()
    assert (
        "check"
        in db.fails(
            f"INSERT INTO learning_resources (lesson_id, kind, title, storage_key, license) "
            f"VALUES ('{lid}', 'image', 'Cœur', 'img/heart.png', 'CC-BY-4.0')"
        ).lower()
    )
    assert (
        "check"
        in db.fails(
            f"INSERT INTO learning_resources (lesson_id, kind, title, storage_key, license) "
            f"VALUES ('{lid}', 'video', 'Lavage des mains', 'vid/hands.mp4', 'propriétaire')"
        ).lower()
    )


def test_question_requires_explanation(db, specialty_id):
    assert db.fails(
        f"INSERT INTO questions (type, specialty_id, level, difficulty, stem_i18n) "
        f"VALUES ('true_false', '{specialty_id}', 'beginner', 1, '{{\"fr\":\"?\"}}')"
    )


# --------------------------------------------------------------------------- simulations
def _sim(specialty_id: str, extra_cols: str = "", extra_vals: str = "") -> str:
    return (
        f"INSERT INTO clinical_simulations (code, specialty_id, level, title_i18n, virtual_patient, scenario, "
        f"max_score{extra_cols}) VALUES ('s-{new_id()[:8]}', '{specialty_id}', 'beginner', '{{\"fr\":\"Sim\"}}', "
        f'\'{{"name":"Patient fictif"}}\', \'{{"start":"n1","nodes":{{}}}}\', 10{extra_vals})'
    )


def test_simulation_defaults_to_fictional_patient(db, specialty_id):
    db.sql(_sim(specialty_id))
    assert db.sql("SELECT DISTINCT patient_data_origin FROM clinical_simulations") == "fictional"


def test_deidentified_data_requires_approval_reference(db, specialty_id):
    assert "check" in db.fails(_sim(specialty_id, ", patient_data_origin", ", 'deidentified_approved'")).lower()


def test_regulated_act_requires_supervision_notice(db, specialty_id):
    assert "check" in db.fails(_sim(specialty_id, ", involves_regulated_act", ", true")).lower()
    db.sql(
        _sim(
            specialty_id,
            ", involves_regulated_act, supervision_notice_i18n",
            ', true, \'{"fr":"Geste réalisé uniquement sous supervision, après formation pratique."}\'',
        )
    )


# --------------------------------------------------------------------------- compétences & attestations
def test_practice_verification_requires_another_person(db, make_user, grant_role):
    student = make_user()
    grant_role(student, "instructor")  # même habilité, on ne se valide pas soi-même
    cid = new_id()
    db.sql(
        f"INSERT INTO competencies (id, code, specialty_id, title_i18n, domain, risk_level) "
        f"SELECT '{cid}', 'c-{cid[:8]}', id, '{{\"fr\":\"Prise de pouls\"}}', 'skill', 'moderate' "
        f"FROM specialties WHERE code='care_assistant_core'"
    )
    assert (
        "check"
        in db.fails(
            f"INSERT INTO competency_assessments (user_id, competency_id, level) VALUES ('{student}', '{cid}', 'verified_in_practice')"
        ).lower()
    )
    assert (
        "check"
        in db.fails(
            f"INSERT INTO competency_assessments (user_id, competency_id, level, verified_by, verified_at) "
            f"VALUES ('{student}', '{cid}', 'verified_in_practice', '{student}', now())"
        ).lower()
    )
    db.sql(
        f"INSERT INTO competency_assessments (user_id, competency_id, level) VALUES ('{student}', '{cid}', 'theory_proficient')"
    )


def test_certificates_are_internal_attestations_only(db, make_user):
    uid = make_user()
    mid = db.sql("SELECT id FROM modules LIMIT 1")
    disclaimer = (
        '{"fr":"Attestation interne de formation. Ne constitue ni un diplôme ni une autorisation d\'\'exercer."}'
    )
    assert (
        "check"
        in db.fails(
            f"INSERT INTO certificates (user_id, module_id, kind, serial, title_snapshot, disclaimer_i18n) "
            f"VALUES ('{uid}', '{mid}', 'diploma', 'X-1', 'Test', '{disclaimer}')"
        ).lower()
    )
    assert db.fails(
        f"INSERT INTO certificates (user_id, module_id, serial, title_snapshot) VALUES ('{uid}', '{mid}', 'X-2', 'Test')"
    )
    db.sql(
        f"INSERT INTO certificates (user_id, module_id, serial, title_snapshot, disclaimer_i18n) "
        f"VALUES ('{uid}', '{mid}', 'X-3', 'Test', '{disclaimer}')"
    )


# --------------------------------------------------------------------------- examens
def test_attempt_targets_exactly_one_assessment(db, make_user):
    uid = make_user()
    assert "check" in db.fails(f"INSERT INTO exam_attempts (user_id, form_snapshot) VALUES ('{uid}', '{{}}')").lower()


def test_timed_exam_requires_time_limit_and_osce_requires_rubric(db):
    assert (
        "check"
        in db.fails("INSERT INTO exams (code, kind, title_i18n) VALUES ('t1', 'timed', '{\"fr\":\"Examen\"}')").lower()
    )
    assert (
        "check"
        in db.fails("INSERT INTO exams (code, kind, title_i18n) VALUES ('o1', 'osce', '{\"fr\":\"ECOS\"}')").lower()
    )


# --------------------------------------------------------------------------- IA
def test_ai_source_needs_approver_and_one_active_prompt(db):
    rid = db.sql("SELECT id FROM medical_references LIMIT 1")
    assert (
        "check"
        in db.fails(
            f"INSERT INTO ai_knowledge_sources (reference_id, locale, storage_key, sha256, license, status) "
            f"VALUES ('{rid}', 'fr', 'k', '{'a' * 64}', 'CC-BY', 'approved')"
        ).lower()
    )
    db.sql(
        "INSERT INTO ai_prompt_templates (key, version, locale, template, status) VALUES ('tutor.system', 1, 'fr', 'v1', 'active')"
    )
    assert "duplicate key" in db.fails(
        "INSERT INTO ai_prompt_templates (key, version, locale, template, status) VALUES ('tutor.system', 2, 'fr', 'v2', 'active')"
    )


# --------------------------------------------------------------------------- facturation
def test_free_plan_cannot_be_priced_and_no_live_stripe_by_default(db):
    assert (
        "check"
        in db.fails(
            "INSERT INTO plans (code, tier, name_i18n, price_cents, ai_daily_message_quota) VALUES ('f2', 'free', '{\"fr\":\"x\"}', 100, 1)"
        ).lower()
    )
    assert db.sql("SELECT count(*) FROM plans WHERE stripe_price_id IS NOT NULL") == "0"


def test_payments_survive_account_erasure(db, make_user):
    uid = make_user()
    pid = db.sql("SELECT id FROM plans WHERE code='premium_monthly'")
    sid = new_id()
    db.sql(f"INSERT INTO subscriptions (id, user_id, plan_id, status) VALUES ('{sid}', '{uid}', '{pid}', 'canceled')")
    db.sql(
        f"INSERT INTO payments (subscription_id, amount_cents, currency, status, provider, paid_at) "
        f"VALUES ('{sid}', 1999, 'CAD', 'succeeded', 'manual', now())"
    )
    db.sql(f"DELETE FROM users WHERE id = '{uid}'")
    assert db.sql(f"SELECT count(*) FROM payments WHERE subscription_id = '{sid}'") == "1"
    assert "foreign key" in db.fails(f"DELETE FROM subscriptions WHERE id = '{sid}'")


def test_active_subscription_must_have_owner(db):
    pid = db.sql("SELECT id FROM plans WHERE code='premium_monthly'")
    assert "check" in db.fails(f"INSERT INTO subscriptions (plan_id, status) VALUES ('{pid}', 'active')").lower()


# --------------------------------------------------------------------------- RGPD / Loi 25 & audit
def test_account_erasure_cascades_learning_data(db, make_user, make_lesson):
    uid, lid = make_user(), make_lesson()
    db.sql(f"INSERT INTO profiles (user_id, display_name) VALUES ('{uid}', 'Awa')")
    db.sql(f"INSERT INTO student_progress (user_id, lesson_id) VALUES ('{uid}', '{lid}')")
    db.sql(f"INSERT INTO ai_conversations (user_id, mode, locale) VALUES ('{uid}', 'tutor', 'fr')")
    db.sql(
        f"INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id) VALUES ('{uid}', 'user.login', 'user', '{uid}')"
    )
    db.sql(f"DELETE FROM users WHERE id = '{uid}'")
    for t in ("profiles", "student_progress", "ai_conversations"):
        assert db.sql(f"SELECT count(*) FROM {t} WHERE user_id = '{uid}'") == "0", t
    assert db.sql(f"SELECT count(*) FROM audit_logs WHERE actor_user_id = '{uid}'") == "1"


def test_audit_log_is_append_only_and_hash_chained(db):
    for i in range(3):
        db.sql(f"INSERT INTO audit_logs (action, entity_type, entity_id) VALUES ('lesson.publish', 'lesson', 'L{i}')")
    assert "ajout seul" in db.fails("UPDATE audit_logs SET action = 'x.y'")
    assert "ajout seul" in db.fails("DELETE FROM audit_logs")
    assert "ajout seul" in db.fails("TRUNCATE audit_logs")
    broken = db.sql(
        "SELECT count(*) FROM (SELECT prev_hash, lag(hash) OVER (ORDER BY id) AS expected "
        "FROM audit_logs) s WHERE prev_hash IS DISTINCT FROM expected"
    )
    assert broken == "0"
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in db.sql("SELECT hash FROM audit_logs").splitlines())


def test_updated_at_is_refreshed(db):
    before = db.sql("SELECT updated_at FROM specialties WHERE code='oncology'")
    db.sql("UPDATE specialties SET sort_order = sort_order + 1 WHERE code='oncology'")
    after = db.sql("SELECT updated_at FROM specialties WHERE code='oncology'")
    assert after > before

-- =============================================================================
-- Retour arrière de la migration 0005 (moteur pédagogique).
-- Supprime les tables, colonnes, déclencheurs et fonctions ajoutés par 0005.
-- ATTENTION : supprime les éditions, inscriptions, événements d'apprentissage et décisions
-- éditoriales enregistrés depuis 0005 ; à n'utiliser qu'en développement ou après sauvegarde.
-- Les états propres à 0005 sont ramenés aux codes du lot 1 (medical_review → in_review).
-- =============================================================================
DROP TRIGGER IF EXISTS trg_student_progress_check ON student_progress;
DROP FUNCTION IF EXISTS nl_student_progress_check();
DROP INDEX IF EXISTS ux_student_progress_enrollment_lesson;
DROP INDEX IF EXISTS ux_student_progress_enrollment_module;
DROP INDEX IF EXISTS ux_student_progress_enrollment_program;
ALTER TABLE student_progress
  DROP CONSTRAINT IF EXISTS fk_student_progress_enrollment,
  DROP CONSTRAINT IF EXISTS ck_student_progress_version,
  DROP COLUMN IF EXISTS enrollment_id,
  DROP COLUMN IF EXISTS lesson_version_id,
  DROP COLUMN IF EXISTS last_block_id;

DROP TABLE IF EXISTS learning_events;
DROP FUNCTION IF EXISTS nl_learning_event_check();
DROP TABLE IF EXISTS enrollments;
DROP FUNCTION IF EXISTS nl_enrollment_guard();

DROP TRIGGER IF EXISTS trg_modules_catalog_guard ON modules;
DROP FUNCTION IF EXISTS nl_module_catalog_guard();
DROP TRIGGER IF EXISTS trg_programs_catalog_guard ON programs;
DROP FUNCTION IF EXISTS nl_program_catalog_guard();

DROP TRIGGER IF EXISTS trg_lesson_versions_delete ON lesson_versions;
DROP TRIGGER IF EXISTS trg_lesson_versions_90_log ON lesson_versions;
DROP TRIGGER IF EXISTS trg_lesson_versions_10_workflow ON lesson_versions;
DROP TRIGGER IF EXISTS trg_lesson_versions_10_insert ON lesson_versions;
DROP TRIGGER IF EXISTS trg_lesson_version_references_guard ON lesson_version_references;

DROP TABLE IF EXISTS program_releases;
DROP FUNCTION IF EXISTS nl_program_release_after();
DROP FUNCTION IF EXISTS nl_program_release_update();
DROP FUNCTION IF EXISTS nl_program_release_insert();
DROP FUNCTION IF EXISTS nl_program_release_delete();
DROP FUNCTION IF EXISTS nl_validate_release(uuid, jsonb, text, uuid);

DROP TABLE IF EXISTS editorial_decisions;
DROP FUNCTION IF EXISTS nl_editorial_decisions_guard();
DROP FUNCTION IF EXISTS nl_log_decision(text, uuid, text, text, uuid, char);
DROP FUNCTION IF EXISTS nl_check_transition_actor(text, text, uuid, uuid, uuid);
DROP FUNCTION IF EXISTS nl_transition_allowed(text, text);
DROP FUNCTION IF EXISTS nl_lesson_version_log();
DROP FUNCTION IF EXISTS nl_lesson_version_update();
DROP FUNCTION IF EXISTS nl_lesson_version_insert();
DROP FUNCTION IF EXISTS nl_lesson_version_delete();
DROP FUNCTION IF EXISTS nl_lesson_version_refs_guard();
DROP FUNCTION IF EXISTS nl_manifest_version_ids(jsonb);
DROP FUNCTION IF EXISTS nl_lesson_version_sha256(uuid);
DROP FUNCTION IF EXISTS nl_lesson_org(uuid);
DROP FUNCTION IF EXISTS nl_actor();

ALTER TABLE programs DROP COLUMN IF EXISTS is_medical;

ALTER TABLE lesson_versions
  DROP CONSTRAINT IF EXISTS ck_lesson_versions_published_by,
  DROP CONSTRAINT IF EXISTS ck_lesson_versions_approved_hash,
  DROP COLUMN IF EXISTS content_sha256,
  DROP COLUMN IF EXISTS published_by,
  DROP COLUMN IF EXISTS submitted_at;
ALTER TABLE lesson_versions DROP CONSTRAINT IF EXISTS ck_lesson_versions_status;
UPDATE lesson_versions SET status = 'in_review' WHERE status = 'medical_review';
ALTER TABLE lesson_versions ADD CONSTRAINT lesson_versions_status_check
  CHECK (status IN ('draft','in_review','approved','published','archived'));

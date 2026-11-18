DROP TRIGGER IF EXISTS trg_cohorts_instructor_org ON cohorts;
DROP TRIGGER IF EXISTS trg_exam_questions_org ON exam_questions;
DROP TRIGGER IF EXISTS trg_quiz_questions_org ON quiz_questions;
DROP TRIGGER IF EXISTS trg_program_modules_org ON program_modules;
DROP TRIGGER IF EXISTS trg_competency_verifier ON competency_assessments;
DROP TRIGGER IF EXISTS trg_ai_sources_approver ON ai_knowledge_sources;
DROP TRIGGER IF EXISTS trg_professions_validator ON professions;
DROP TRIGGER IF EXISTS trg_simulations_reviewer ON clinical_simulations;
DROP TRIGGER IF EXISTS trg_questions_reviewer ON questions;
DROP TRIGGER IF EXISTS trg_lesson_versions_reviewer ON lesson_versions;
DROP FUNCTION IF EXISTS nl_check_cohort_instructor_org(), nl_check_assessment_question_org(),
  nl_check_program_module_org(), nl_check_competency_verifier(), nl_check_reviewer(),
  nl_user_organizations(uuid), nl_user_has_permission(uuid, text, uuid);

-- Migration 0001 — retour arrière (DÉTRUIT TOUTES LES DONNÉES).
-- Généré à partir du schéma 0001 ; ordre sans importance grâce à CASCADE.
DROP TABLE IF EXISTS ai_conversations, ai_knowledge_chunks, ai_knowledge_sources, ai_messages, 
ai_prompt_templates, answers, attempt_responses, audit_logs, auth_sessions, badges, certificates, 
clinical_simulations, cohort_members, cohorts, competencies, competency_assessments, 
competency_scopes, content_issue_reports, countries, exam_attempts, exam_questions, exams, 
instructors, jurisdictions, learning_objectives, learning_resources, lesson_version_references, 
lesson_versions, lessons, locales, medical_references, module_prerequisites, modules, 
objective_competencies, organizations, payments, permissions, plans, professions, profiles, 
program_modules, programs, question_references, questions, quiz_questions, quizzes, review_cards, 
role_permissions, roles, simulation_attempts, simulation_competencies, specialties, 
student_progress, study_plan_items, study_plans, subscriptions, user_badges, user_roles, users, 
xp_events CASCADE;
DROP FUNCTION IF EXISTS nl_audit_chain(), nl_forbid_modification(), nl_has_base_locale(jsonb), nl_set_updated_at();
-- Les extensions (pgcrypto, citext, pg_trgm) sont conservées volontairement.

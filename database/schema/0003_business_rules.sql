-- =============================================================================
-- Migration 0003 — Renforcement des règles métier (Phase 1.1)
-- Additive : aucune table ni colonne existante n'est modifiée.
--   1. Les relecteurs/validateurs doivent détenir la permission adéquate.
--   2. La validation pratique d'une compétence est réservée à un formateur
--      autorisé, globalement ou dans un établissement de l'apprenant.
--   3. Isolation des contenus privés d'un établissement.
-- =============================================================================

-- Vrai si l'utilisateur, actif et non supprimé, détient la permission.
-- p_org NULL : seul un rôle global (organization_id NULL) compte.
-- p_org renseigné : un rôle global OU un rôle dans cet établissement compte.
CREATE OR REPLACE FUNCTION nl_user_has_permission(p_user uuid, p_perm text, p_org uuid DEFAULT NULL)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
    FROM users u
    JOIN user_roles ur ON ur.user_id = u.id
    JOIN role_permissions rp ON rp.role_id = ur.role_id
    WHERE u.id = p_user
      AND u.status = 'active' AND u.deleted_at IS NULL
      AND rp.permission_code = p_perm
      AND (ur.organization_id IS NULL OR (p_org IS NOT NULL AND ur.organization_id = p_org))
  )
$$;

-- Établissements auxquels un utilisateur est rattaché (rôle ou cohorte).
CREATE OR REPLACE FUNCTION nl_user_organizations(p_user uuid)
RETURNS SETOF uuid LANGUAGE sql STABLE AS $$
  SELECT organization_id FROM user_roles WHERE user_id = p_user AND organization_id IS NOT NULL
  UNION
  SELECT c.organization_id FROM cohort_members cm JOIN cohorts c ON c.id = cm.cohort_id WHERE cm.user_id = p_user
$$;

-- -----------------------------------------------------------------------------
-- 1. Relecture et validation par une personne habilitée
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_check_reviewer() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_col  text := TG_ARGV[0];          -- colonne du relecteur
  v_perm text := TG_ARGV[1];          -- permission exigée
  v_user uuid;
  v_old  uuid;
BEGIN
  v_user := (to_jsonb(NEW) ->> v_col)::uuid;
  IF TG_OP = 'UPDATE' THEN
    v_old := (to_jsonb(OLD) ->> v_col)::uuid;
    IF v_user IS NOT DISTINCT FROM v_old THEN
      RETURN NEW;                      -- relecteur inchangé : rien à revérifier
    END IF;
  END IF;
  IF v_user IS NOT NULL AND NOT nl_user_has_permission(v_user, v_perm) THEN
    RAISE EXCEPTION 'Utilisateur % non habilité (%) pour %.%', v_user, v_perm, TG_TABLE_NAME, v_col
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_lesson_versions_reviewer BEFORE INSERT OR UPDATE OF reviewed_by ON lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_check_reviewer('reviewed_by', 'content:review');
CREATE TRIGGER trg_questions_reviewer BEFORE INSERT OR UPDATE OF reviewed_by ON questions
  FOR EACH ROW EXECUTE FUNCTION nl_check_reviewer('reviewed_by', 'content:review');
CREATE TRIGGER trg_simulations_reviewer BEFORE INSERT OR UPDATE OF reviewed_by ON clinical_simulations
  FOR EACH ROW EXECUTE FUNCTION nl_check_reviewer('reviewed_by', 'content:review');
CREATE TRIGGER trg_professions_validator BEFORE INSERT OR UPDATE OF validated_by ON professions
  FOR EACH ROW EXECUTE FUNCTION nl_check_reviewer('validated_by', 'content:review');
CREATE TRIGGER trg_ai_sources_approver BEFORE INSERT OR UPDATE OF approved_by ON ai_knowledge_sources
  FOR EACH ROW EXECUTE FUNCTION nl_check_reviewer('approved_by', 'content:review');

-- -----------------------------------------------------------------------------
-- 2. Validation pratique : formateur habilité, global ou du même établissement
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_check_competency_verifier() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.verified_by IS NULL THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.verified_by IS NOT DISTINCT FROM OLD.verified_by THEN
    RETURN NEW;
  END IF;
  IF nl_user_has_permission(NEW.verified_by, 'competency:verify') THEN
    RETURN NEW;
  END IF;
  IF EXISTS (SELECT 1 FROM nl_user_organizations(NEW.user_id) AS o(org)
             WHERE nl_user_has_permission(NEW.verified_by, 'competency:verify', o.org)) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Utilisateur % non habilité à valider en pratique les compétences de %', NEW.verified_by, NEW.user_id
    USING ERRCODE = 'check_violation';
END $$;

CREATE TRIGGER trg_competency_verifier BEFORE INSERT OR UPDATE OF verified_by ON competency_assessments
  FOR EACH ROW EXECUTE FUNCTION nl_check_competency_verifier();

-- -----------------------------------------------------------------------------
-- 3. Isolation des contenus d'établissement
-- -----------------------------------------------------------------------------
-- Un programme ne peut inclure que des modules publics ou du même établissement.
CREATE OR REPLACE FUNCTION nl_check_program_module_org() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p_org uuid; m_org uuid;
BEGIN
  SELECT organization_id INTO p_org FROM programs WHERE id = NEW.program_id;
  SELECT organization_id INTO m_org FROM modules  WHERE id = NEW.module_id;
  IF m_org IS NOT NULL AND m_org IS DISTINCT FROM p_org THEN
    RAISE EXCEPTION 'Module privé % hors de l''établissement du programme %', NEW.module_id, NEW.program_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_program_modules_org BEFORE INSERT OR UPDATE ON program_modules
  FOR EACH ROW EXECUTE FUNCTION nl_check_program_module_org();

-- Une question privée ne peut servir que dans un quiz/examen du même établissement.
CREATE OR REPLACE FUNCTION nl_check_assessment_question_org() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a_org uuid; q_org uuid;
BEGIN
  IF TG_TABLE_NAME = 'quiz_questions' THEN
    SELECT m.organization_id INTO a_org FROM quizzes qz JOIN modules m ON m.id = qz.module_id WHERE qz.id = NEW.quiz_id;
  ELSE
    SELECT organization_id INTO a_org FROM exams WHERE id = NEW.exam_id;
  END IF;
  SELECT organization_id INTO q_org FROM questions WHERE id = NEW.question_id;
  IF q_org IS NOT NULL AND q_org IS DISTINCT FROM a_org THEN
    RAISE EXCEPTION 'Question privée % utilisée hors de son établissement', NEW.question_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_quiz_questions_org BEFORE INSERT OR UPDATE ON quiz_questions
  FOR EACH ROW EXECUTE FUNCTION nl_check_assessment_question_org();
CREATE TRIGGER trg_exam_questions_org BEFORE INSERT OR UPDATE ON exam_questions
  FOR EACH ROW EXECUTE FUNCTION nl_check_assessment_question_org();

-- Le formateur d'une cohorte appartient à l'établissement de la cohorte.
CREATE OR REPLACE FUNCTION nl_check_cohort_instructor_org() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.instructor_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM instructors i WHERE i.user_id = NEW.instructor_id AND i.organization_id = NEW.organization_id) THEN
    RAISE EXCEPTION 'Le formateur % n''appartient pas à l''établissement %', NEW.instructor_id, NEW.organization_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_cohorts_instructor_org BEFORE INSERT OR UPDATE OF instructor_id, organization_id ON cohorts
  FOR EACH ROW EXECUTE FUNCTION nl_check_cohort_instructor_org();

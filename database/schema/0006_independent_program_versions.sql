-- =============================================================================
-- Migration 0006 — Versions de modules indépendantes par édition de programme
-- (décision D12, ADR-0010 ; étape 2.2.2-a). Additive et réversible.
--
--   1. module_versions : instantané immuable d'un module (leçons, ordre, caractère obligatoire,
--      version exacte de chaque leçon par langue), soumis au même workflow à cinq états.
--   2. Associations relationnelles vérifiées par clés étrangères composées :
--        version de module → leçons (module_version_lessons)
--        version de module × leçon × langue → version de leçon (module_version_lesson_versions)
--        édition → versions de modules (program_release_modules)
--        prérequis figés par édition (program_release_prerequisites)
--   3. Indépendance des programmes : suppression de l'index « une seule version publiée par
--      leçon × langue × juridiction » (0001) et de l'archivage implicite / du blocage croisé
--      (0005) ; une version citée par une édition publiée ne peut plus être archivée.
--   4. Sources médicales explicitement vérifiées (verified_by, par un relecteur habilité).
--   5. Compatibilité : les éditions créées sous 0005 restent valides (structure = 'manifest_v1') ;
--      toute nouvelle édition cite des versions de modules (structure = 'module_versions').
--
-- Aucune migration historique n'est modifiée. Les fonctions de 0005 qui lisaient le manifeste
-- JSON sont redéfinies pour lire nl_release_lessons(), commune aux deux structures ; le retour
-- arrière restaure leurs corps d'origine. Les questions et simulations (lot 2.3) ne changent pas.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Contraintes support (aucune donnée modifiée)
-- -----------------------------------------------------------------------------
ALTER TABLE lessons ADD CONSTRAINT uq_lessons_id_module UNIQUE (id, module_id);
ALTER TABLE lesson_versions ADD CONSTRAINT uq_lesson_versions_id_lesson_locale UNIQUE (id, lesson_id, locale);

-- Journal éditorial : nouvelle entité « module_version ».
DO $$
DECLARE c text;
BEGIN
  SELECT conname INTO c FROM pg_constraint
  WHERE conrelid = 'editorial_decisions'::regclass AND contype = 'c'
    AND pg_get_constraintdef(oid) LIKE '%entity_type%program_release%';
  EXECUTE format('ALTER TABLE editorial_decisions DROP CONSTRAINT %I', c);
END $$;
ALTER TABLE editorial_decisions ADD CONSTRAINT ck_editorial_decisions_entity_type
  CHECK (entity_type IN ('lesson_version','module_version','program_release'));

-- Indépendance des programmes : plusieurs versions publiées d'une même leçon peuvent coexister,
-- chacune diffusée par les éditions qui la citent explicitement.
DROP INDEX ux_lesson_versions_one_published;
CREATE INDEX ix_lesson_versions_published ON lesson_versions (lesson_id, locale) WHERE status = 'published';

-- -----------------------------------------------------------------------------
-- 2. Sources médicales explicitement vérifiées
-- -----------------------------------------------------------------------------
ALTER TABLE medical_references ADD COLUMN verified_by uuid REFERENCES users(id) ON DELETE RESTRICT;
COMMENT ON COLUMN medical_references.verified_by IS
  'Relecteur habilité (content:review) ayant vérifié la source ; posé par le déclencheur depuis nl.actor_id';

CREATE OR REPLACE FUNCTION nl_reference_verification() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor uuid;
BEGIN
  IF NEW.last_verified_at IS NULL THEN
    IF NEW.verified_by IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.verified_by IS DISTINCT FROM OLD.verified_by) THEN
      RAISE EXCEPTION 'Vérification sans date : refusée' USING ERRCODE = 'check_violation', HINT = 'nl:source_unverified';
    END IF;
    NEW.verified_by := NULL;
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' OR NEW.last_verified_at IS DISTINCT FROM OLD.last_verified_at
     OR NEW.verified_by IS DISTINCT FROM OLD.verified_by THEN
    v_actor := nl_actor();
    IF NOT nl_user_has_permission(v_actor, 'content:review', NULL) THEN
      RAISE EXCEPTION 'Vérification d''une source réservée à un relecteur habilité'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:reviewer_required';
    END IF;
    IF NEW.last_verified_at > current_date THEN
      RAISE EXCEPTION 'Date de vérification future' USING ERRCODE = 'check_violation', HINT = 'nl:invalid_date';
    END IF;
    NEW.verified_by := v_actor;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_medical_references_verification BEFORE INSERT OR UPDATE ON medical_references
  FOR EACH ROW EXECUTE FUNCTION nl_reference_verification();

-- Source recevable pour un contenu médical : active et explicitement vérifiée.
CREATE OR REPLACE FUNCTION nl_has_verified_source(p_lesson_version uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM lesson_version_references r JOIN medical_references mr ON mr.id = r.reference_id
                 WHERE r.lesson_version_id = p_lesson_version AND mr.status = 'active'
                   AND mr.last_verified_at IS NOT NULL AND mr.verified_by IS NOT NULL)
$$;

-- -----------------------------------------------------------------------------
-- 3. Versions de modules
-- -----------------------------------------------------------------------------
CREATE TABLE module_versions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  module_id       uuid NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
  version_no      integer NOT NULL CHECK (version_no >= 1),
  status          text NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','medical_review','approved','published','archived')),
  change_summary  text CHECK (change_summary IS NULL OR length(change_summary) <= 4000),
  author_id       uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  submitted_at    timestamptz,
  reviewed_by     uuid REFERENCES users(id) ON DELETE RESTRICT,
  reviewed_at     timestamptz,
  published_by    uuid REFERENCES users(id) ON DELETE RESTRICT,
  published_at    timestamptz,
  archived_at     timestamptz,
  content_sha256  char(64) CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (module_id, version_no),
  UNIQUE (id, module_id),                                     -- cible des clés composées
  CHECK (status NOT IN ('approved','published') OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
                                                   AND content_sha256 IS NOT NULL)),
  CHECK (reviewed_by IS NULL OR reviewed_by <> author_id),
  CHECK (status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL)),
  CHECK (status <> 'archived' OR archived_at IS NOT NULL)
);
CREATE INDEX ix_module_versions_status ON module_versions (module_id, status);
CREATE TRIGGER trg_module_versions_updated_at BEFORE UPDATE ON module_versions
  FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at();
COMMENT ON TABLE module_versions IS
  'Version immuable d''un module (leçons et versions exactes) ; citée explicitement par les éditions de programme';

CREATE TABLE module_version_lessons (
  module_version_id uuid NOT NULL,
  module_id         uuid NOT NULL,
  lesson_id         uuid NOT NULL,
  position          integer NOT NULL CHECK (position >= 0),
  required          boolean NOT NULL DEFAULT true,
  PRIMARY KEY (module_version_id, lesson_id),
  UNIQUE (module_version_id, position) DEFERRABLE INITIALLY DEFERRED,
  -- La leçon appartient au module de la version (par construction).
  FOREIGN KEY (module_version_id, module_id) REFERENCES module_versions (id, module_id) ON DELETE CASCADE,
  FOREIGN KEY (lesson_id, module_id) REFERENCES lessons (id, module_id) ON DELETE RESTRICT
);

CREATE TABLE module_version_lesson_versions (
  module_version_id uuid NOT NULL,
  lesson_id         uuid NOT NULL,
  locale            text NOT NULL,
  lesson_version_id uuid NOT NULL,
  PRIMARY KEY (module_version_id, lesson_id, locale),
  FOREIGN KEY (module_version_id, lesson_id) REFERENCES module_version_lessons (module_version_id, lesson_id)
    ON DELETE CASCADE,
  -- La version appartient à la leçon et est rédigée dans la langue annoncée (par construction).
  FOREIGN KEY (lesson_version_id, lesson_id, locale) REFERENCES lesson_versions (id, lesson_id, locale)
    ON DELETE RESTRICT
);
CREATE INDEX ix_mvlv_lesson_version ON module_version_lesson_versions (lesson_version_id);

-- -----------------------------------------------------------------------------
-- 4. Éditions : structure, versions de modules, prérequis figés
-- -----------------------------------------------------------------------------
-- Les éditions existantes (0005) gardent leur manifeste ; toute nouvelle édition est relationnelle.
ALTER TABLE program_releases
  ADD COLUMN structure text NOT NULL DEFAULT 'manifest_v1' CHECK (structure IN ('manifest_v1','module_versions'));
ALTER TABLE program_releases ALTER COLUMN structure SET DEFAULT 'module_versions';
COMMENT ON COLUMN program_releases.structure IS
  'manifest_v1 : édition historique (0005, manifeste JSON figé) ; module_versions : versions de modules citées explicitement';

CREATE TABLE program_release_modules (
  release_id        uuid NOT NULL REFERENCES program_releases(id) ON DELETE CASCADE,
  module_id         uuid NOT NULL,
  module_version_id uuid NOT NULL,
  position          integer NOT NULL CHECK (position >= 0),
  required          boolean NOT NULL DEFAULT true,
  PRIMARY KEY (release_id, module_id),                       -- une version par module dans l'édition
  UNIQUE (release_id, module_version_id),
  UNIQUE (release_id, position) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (module_version_id, module_id) REFERENCES module_versions (id, module_id) ON DELETE RESTRICT
);
CREATE INDEX ix_program_release_modules_version ON program_release_modules (module_version_id);

CREATE TABLE program_release_prerequisites (
  release_id             uuid NOT NULL,
  module_id              uuid NOT NULL,
  prerequisite_module_id uuid NOT NULL,
  PRIMARY KEY (release_id, module_id, prerequisite_module_id),
  CHECK (module_id <> prerequisite_module_id),
  -- Les deux modules appartiennent à la même édition (par construction).
  FOREIGN KEY (release_id, module_id) REFERENCES program_release_modules (release_id, module_id) ON DELETE CASCADE,
  FOREIGN KEY (release_id, prerequisite_module_id) REFERENCES program_release_modules (release_id, module_id)
    ON DELETE CASCADE
);

-- Traçabilité de la version de module suivie (historique et progression).
ALTER TABLE learning_events ADD COLUMN module_version_id uuid REFERENCES module_versions(id) ON DELETE RESTRICT;
ALTER TABLE student_progress ADD COLUMN module_version_id uuid REFERENCES module_versions(id) ON DELETE RESTRICT;
CREATE INDEX ix_learning_events_module_version ON learning_events (module_version_id) WHERE module_version_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 5. Lecture unifiée du contenu d'une édition (deux structures)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_release_lessons(p_release uuid)
RETURNS TABLE (module_id uuid, module_version_id uuid, module_position integer, module_required boolean,
               lesson_id uuid, lesson_position integer, locale text, lesson_version_id uuid)
LANGUAGE sql STABLE AS $$
  SELECT (m.value ->> 'module_id')::uuid, NULL::uuid, (m.value ->> 'position')::integer,
         (m.value ->> 'required')::boolean, (l.value ->> 'lesson_id')::uuid, (l.value ->> 'position')::integer,
         v.key, (v.value #>> '{}')::uuid
  FROM program_releases r,
       jsonb_array_elements(CASE WHEN jsonb_typeof(r.manifest -> 'modules') = 'array'
                                 THEN r.manifest -> 'modules' ELSE '[]'::jsonb END) m,
       jsonb_array_elements(CASE WHEN jsonb_typeof(m.value -> 'lessons') = 'array'
                                 THEN m.value -> 'lessons' ELSE '[]'::jsonb END) l,
       jsonb_each(CASE WHEN jsonb_typeof(l.value -> 'versions') = 'object'
                       THEN l.value -> 'versions' ELSE '{}'::jsonb END) v
  WHERE r.id = p_release AND r.structure = 'manifest_v1'
  UNION ALL
  SELECT rm.module_id, rm.module_version_id, rm.position, rm.required, ml.lesson_id, ml.position,
         mlv.locale, mlv.lesson_version_id
  FROM program_release_modules rm
  JOIN module_version_lessons ml ON ml.module_version_id = rm.module_version_id
  JOIN module_version_lesson_versions mlv
    ON mlv.module_version_id = ml.module_version_id AND mlv.lesson_id = ml.lesson_id
  WHERE rm.release_id = p_release
$$;

-- Une version (de leçon ou de module) est-elle citée par une édition publiée ?
CREATE OR REPLACE FUNCTION nl_lesson_version_in_published_release(p_lesson_version uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM program_releases r
                 WHERE r.status = 'published'
                   AND p_lesson_version IN (SELECT x.lesson_version_id FROM nl_release_lessons(r.id) x))
$$;
CREATE OR REPLACE FUNCTION nl_module_version_in_published_release(p_module_version uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM program_release_modules rm JOIN program_releases r ON r.id = rm.release_id
                 WHERE rm.module_version_id = p_module_version AND r.status = 'published')
$$;

-- Empreinte d'une version de module : structure et empreintes des versions de leçon citées.
CREATE OR REPLACE FUNCTION nl_module_version_sha256(p_id uuid) RETURNS char(64) LANGUAGE sql STABLE AS $$
  SELECT encode(digest(
           mv.module_id::text || chr(31) || mv.version_no || chr(31) ||
           coalesce((SELECT string_agg(ml.lesson_id || ':' || ml.position || ':' || ml.required || ':' ||
                                       mlv.locale || ':' || mlv.lesson_version_id || ':' ||
                                       coalesce(lv.content_sha256, '-'), ',' ORDER BY ml.position, mlv.locale)
                     FROM module_version_lessons ml
                     JOIN module_version_lesson_versions mlv
                       ON mlv.module_version_id = ml.module_version_id AND mlv.lesson_id = ml.lesson_id
                     JOIN lesson_versions lv ON lv.id = mlv.lesson_version_id
                     WHERE ml.module_version_id = mv.id), ''),
           'sha256'), 'hex')
  FROM module_versions mv WHERE mv.id = p_id
$$;

-- Empreinte d'une édition relationnelle : versions de modules (et leurs empreintes), prérequis.
CREATE OR REPLACE FUNCTION nl_release_v2_sha256(p_id uuid) RETURNS char(64) LANGUAGE sql STABLE AS $$
  SELECT encode(digest(
           r.program_id::text || chr(31) || r.release_no || chr(31) ||
           coalesce((SELECT string_agg(rm.position || ':' || rm.module_version_id || ':' || rm.required || ':' ||
                                       coalesce(mv.content_sha256, '-'), ',' ORDER BY rm.position)
                     FROM program_release_modules rm JOIN module_versions mv ON mv.id = rm.module_version_id
                     WHERE rm.release_id = r.id), '') || chr(31) ||
           coalesce((SELECT string_agg(p.module_id || '>' || p.prerequisite_module_id, ','
                                       ORDER BY p.module_id, p.prerequisite_module_id)
                     FROM program_release_prerequisites p WHERE p.release_id = r.id), ''),
           'sha256'), 'hex')
  FROM program_releases r WHERE r.id = p_id
$$;

-- -----------------------------------------------------------------------------
-- 6. Workflow des versions de modules
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_validate_module_version(p_id uuid, p_stage text, p_reviewer uuid)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE v record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM module_version_lessons WHERE module_version_id = p_id) THEN
    RAISE EXCEPTION 'Version de module vide : au moins une leçon est requise'
      USING ERRCODE = 'check_violation', HINT = 'nl:empty_module_version';
  END IF;
  IF EXISTS (SELECT 1 FROM module_version_lessons ml WHERE ml.module_version_id = p_id
             AND NOT EXISTS (SELECT 1 FROM module_version_lesson_versions x
                             WHERE x.module_version_id = p_id AND x.lesson_id = ml.lesson_id AND x.locale = 'fr')) THEN
    RAISE EXCEPTION 'Version de module : chaque leçon exige une version française'
      USING ERRCODE = 'check_violation', HINT = 'nl:missing_base_locale';
  END IF;
  FOR v IN SELECT lv.* FROM module_version_lesson_versions x JOIN lesson_versions lv ON lv.id = x.lesson_version_id
           WHERE x.module_version_id = p_id LOOP
    IF p_stage = 'submit' AND v.status NOT IN ('medical_review','approved','published') THEN
      RAISE EXCEPTION 'Soumission impossible : version de leçon % encore en « % »', v.id, v.status
        USING ERRCODE = 'check_violation', HINT = 'nl:unsubmitted_component';
    END IF;
    IF p_stage IN ('approve','publish') THEN
      IF v.status NOT IN ('approved','published') THEN
        RAISE EXCEPTION 'Version de leçon % non approuvée (« % »)', v.id, v.status
          USING ERRCODE = 'check_violation', HINT = 'nl:unapproved_component';
      END IF;
      IF v.content_sha256 IS DISTINCT FROM nl_lesson_version_sha256(v.id) THEN
        RAISE EXCEPTION 'Version de leçon % modifiée depuis son approbation', v.id
          USING ERRCODE = 'check_violation', HINT = 'nl:tampered';
      END IF;
    END IF;
    IF p_reviewer IS NOT NULL AND v.author_id = p_reviewer THEN
      RAISE EXCEPTION 'Indépendance : le relecteur est l''auteur de la version de leçon % incluse', v.id
        USING ERRCODE = 'check_violation', HINT = 'nl:reviewer_not_independent';
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION nl_module_version_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_max integer;
BEGIN
  IF NEW.status <> 'draft' THEN
    RAISE EXCEPTION 'Une version de module naît en brouillon (reçu : %)', NEW.status
      USING ERRCODE = 'check_violation', HINT = 'nl:born_draft';
  END IF;
  IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_by, NEW.published_at, NEW.archived_at,
                  NEW.content_sha256, NEW.submitted_at) > 0 THEN
    RAISE EXCEPTION 'Un brouillon de version de module ne porte aucune trace de relecture'
      USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('nl_module_version_' || NEW.module_id::text));
  SELECT max(version_no) INTO v_max FROM module_versions WHERE module_id = NEW.module_id;
  IF NEW.version_no <> coalesce(v_max, 0) + 1 THEN
    RAISE EXCEPTION 'Numéro de version de module attendu : % (reçu : %)', coalesce(v_max, 0) + 1, NEW.version_no
      USING ERRCODE = 'check_violation', HINT = 'nl:version_sequence';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_module_versions_10_insert BEFORE INSERT ON module_versions
  FOR EACH ROW EXECUTE FUNCTION nl_module_version_insert();

CREATE OR REPLACE FUNCTION nl_module_version_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid;
  v_org   uuid;
  v_sha   char(64);
BEGIN
  IF NEW.id <> OLD.id OR NEW.module_id <> OLD.module_id OR NEW.version_no <> OLD.version_no
     OR NEW.author_id <> OLD.author_id OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'Identité d''une version de module non modifiable'
      USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  SELECT organization_id INTO v_org FROM modules WHERE id = OLD.module_id;

  IF NEW.status = OLD.status THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Version de module % en état « % » : modification directe interdite, créer une nouvelle version',
        OLD.version_no, OLD.status USING ERRCODE = 'check_violation', HINT = 'nl:not_draft';
    END IF;
    IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_by, NEW.published_at, NEW.archived_at,
                    NEW.content_sha256, NEW.submitted_at) > 0 THEN
      RAISE EXCEPTION 'Un brouillon de version de module ne porte aucune trace de relecture'
        USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT nl_transition_allowed(OLD.status, NEW.status) THEN
    RAISE EXCEPTION 'Transition interdite : % → %', OLD.status, NEW.status
      USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
  END IF;
  IF NEW.change_summary IS DISTINCT FROM OLD.change_summary THEN
    RAISE EXCEPTION 'Une transition ne modifie pas le contenu' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF (NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
      OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256) AND NEW.status <> 'approved' THEN
    RAISE EXCEPTION 'Champs de relecture non modifiables hors approbation'
      USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
  END IF;
  IF (NEW.published_by IS DISTINCT FROM OLD.published_by OR NEW.published_at IS DISTINCT FROM OLD.published_at)
     AND NEW.status <> 'published' THEN
    RAISE EXCEPTION 'Champs de publication non modifiables hors publication'
      USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
  END IF;

  v_actor := nl_actor();
  PERFORM nl_check_transition_actor(OLD.status, NEW.status, v_actor, v_org, OLD.author_id);

  IF NEW.status = 'medical_review' THEN
    PERFORM nl_validate_module_version(OLD.id, 'submit', NULL);
    NEW.submitted_at := now();
  ELSIF OLD.status = 'medical_review' AND NEW.status = 'draft' THEN
    IF length(btrim(coalesce(current_setting('nl.decision_comment', true), ''))) < 5 THEN
      RAISE EXCEPTION 'Demande de modifications : motif obligatoire (nl.decision_comment)'
        USING ERRCODE = 'check_violation', HINT = 'nl:comment_required';
    END IF;
    NEW.submitted_at := NULL;
  ELSIF NEW.status = 'approved' THEN
    PERFORM nl_validate_module_version(OLD.id, 'approve', v_actor);
    NEW.reviewed_by := v_actor;
    NEW.reviewed_at := now();
    NEW.content_sha256 := nl_module_version_sha256(OLD.id);
  ELSIF NEW.status = 'published' THEN
    v_sha := nl_module_version_sha256(OLD.id);
    IF v_sha IS DISTINCT FROM OLD.content_sha256 THEN
      RAISE EXCEPTION 'Publication refusée : la version de module a changé depuis son approbation'
        USING ERRCODE = 'check_violation', HINT = 'nl:tampered';
    END IF;
    PERFORM nl_validate_module_version(OLD.id, 'publish', OLD.reviewed_by);
    NEW.published_by := v_actor;
    NEW.published_at := now();
  ELSIF NEW.status = 'archived' THEN
    IF nl_module_version_in_published_release(OLD.id) THEN
      RAISE EXCEPTION 'Version de module % citée par une édition publiée : archivage refusé', OLD.version_no
        USING ERRCODE = 'check_violation', HINT = 'nl:in_use';
    END IF;
    NEW.archived_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_module_versions_20_workflow BEFORE UPDATE ON module_versions
  FOR EACH ROW EXECUTE FUNCTION nl_module_version_update();

CREATE OR REPLACE FUNCTION nl_module_version_log() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM nl_log_decision('module_version', NEW.id, OLD.status, NEW.status,
                          (SELECT organization_id FROM modules WHERE id = NEW.module_id),
                          coalesce(NEW.content_sha256, nl_module_version_sha256(NEW.id)));
  RETURN NULL;
END $$;
CREATE TRIGGER trg_module_versions_90_log AFTER UPDATE OF status ON module_versions
  FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION nl_module_version_log();

CREATE OR REPLACE FUNCTION nl_module_version_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Version de module « % » non supprimable : historique éditorial', OLD.status
      USING ERRCODE = 'check_violation', HINT = 'nl:history';
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER trg_module_versions_delete BEFORE DELETE ON module_versions
  FOR EACH ROW EXECUTE FUNCTION nl_module_version_delete();

-- Contenu d'une version de module : modifiable seulement en brouillon.
CREATE OR REPLACE FUNCTION nl_module_version_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM module_versions
  WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.module_version_id ELSE NEW.module_version_id END;
  IF v_status IS NOT NULL AND v_status <> 'draft' THEN
    RAISE EXCEPTION 'Contenu d''une version de module « % » non modifiable', v_status
      USING ERRCODE = 'check_violation', HINT = 'nl:not_draft';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.module_version_id <> OLD.module_version_id THEN
    RAISE EXCEPTION 'Rattachement d''une leçon à une version de module non modifiable'
      USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_module_version_lessons_guard BEFORE INSERT OR UPDATE OR DELETE ON module_version_lessons
  FOR EACH ROW EXECUTE FUNCTION nl_module_version_items_guard();
CREATE TRIGGER trg_module_version_lesson_versions_guard BEFORE INSERT OR UPDATE OR DELETE ON module_version_lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_module_version_items_guard();

-- -----------------------------------------------------------------------------
-- 7. Éditions relationnelles : contenu, validation, empreinte
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_program_release_structure_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.structure <> 'module_versions' THEN
    RAISE EXCEPTION 'Nouvelle édition : structure « module_versions » obligatoire (manifeste JSON réservé à l''historique)'
      USING ERRCODE = 'check_violation', HINT = 'nl:legacy_structure';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.structure <> OLD.structure THEN
    RAISE EXCEPTION 'Structure d''une édition non modifiable' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF NEW.structure = 'module_versions' AND NEW.manifest <> '{"modules":[]}'::jsonb THEN
    RAISE EXCEPTION 'Édition relationnelle : le manifeste JSON reste vide (versions de modules citées explicitement)'
      USING ERRCODE = 'check_violation', HINT = 'nl:legacy_structure';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_program_releases_05_structure BEFORE INSERT OR UPDATE ON program_releases
  FOR EACH ROW EXECUTE FUNCTION nl_program_release_structure_guard();

-- Contenu d'une édition relationnelle : modifiable seulement en brouillon ; isolation des établissements.
CREATE OR REPLACE FUNCTION nl_release_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  r       program_releases%ROWTYPE;
  v_morg  uuid;
  v_porg  uuid;
BEGIN
  SELECT * INTO r FROM program_releases
  WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.release_id ELSE NEW.release_id END;
  IF r.id IS NOT NULL AND r.status <> 'draft' THEN
    RAISE EXCEPTION 'Contenu d''une édition « % » non modifiable', r.status
      USING ERRCODE = 'check_violation', HINT = 'nl:not_draft';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  IF r.structure <> 'module_versions' THEN
    RAISE EXCEPTION 'Édition historique (manifeste JSON) : contenu relationnel interdit'
      USING ERRCODE = 'check_violation', HINT = 'nl:legacy_structure';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.release_id <> OLD.release_id THEN
    RAISE EXCEPTION 'Rattachement à une édition non modifiable' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF TG_TABLE_NAME = 'program_release_modules' THEN
    SELECT organization_id INTO v_morg FROM modules WHERE id = NEW.module_id;
    SELECT organization_id INTO v_porg FROM programs WHERE id = r.program_id;
    IF v_morg IS NOT NULL AND v_morg IS DISTINCT FROM v_porg THEN
      RAISE EXCEPTION 'Module privé % hors de l''établissement du programme', NEW.module_id
        USING ERRCODE = 'check_violation', HINT = 'nl:organization_scope';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_program_release_modules_guard BEFORE INSERT OR UPDATE OR DELETE ON program_release_modules
  FOR EACH ROW EXECUTE FUNCTION nl_release_items_guard();
CREATE TRIGGER trg_program_release_prerequisites_guard BEFORE INSERT OR UPDATE OR DELETE ON program_release_prerequisites
  FOR EACH ROW EXECUTE FUNCTION nl_release_items_guard();

CREATE OR REPLACE FUNCTION nl_validate_release_v2(p_release uuid, p_stage text, p_reviewer uuid)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE
  r      program_releases%ROWTYPE;
  v_prog programs%ROWTYPE;
  v_jur  uuid;
  mv     record;
  lv     record;
BEGIN
  SELECT * INTO r FROM program_releases WHERE id = p_release;
  SELECT * INTO v_prog FROM programs WHERE id = r.program_id;
  SELECT jurisdiction_id INTO v_jur FROM professions WHERE id = v_prog.profession_id;

  IF NOT EXISTS (SELECT 1 FROM program_release_modules WHERE release_id = p_release) THEN
    RAISE EXCEPTION 'Édition vide : au moins une version de module est requise'
      USING ERRCODE = 'check_violation', HINT = 'nl:empty_release';
  END IF;
  -- Prérequis figés : aucun cycle.
  IF EXISTS (
       WITH RECURSIVE walk(origin, current, depth) AS (
         SELECT module_id, prerequisite_module_id, 1 FROM program_release_prerequisites WHERE release_id = p_release
         UNION ALL
         SELECT w.origin, p.prerequisite_module_id, w.depth + 1
         FROM walk w JOIN program_release_prerequisites p ON p.release_id = p_release AND p.module_id = w.current
         WHERE w.depth < 100)
       SELECT 1 FROM walk WHERE origin = current) THEN
    RAISE EXCEPTION 'Prérequis circulaires dans l''édition' USING ERRCODE = 'check_violation', HINT = 'nl:prerequisite_cycle';
  END IF;

  FOR mv IN SELECT m.* FROM program_release_modules rm JOIN module_versions m ON m.id = rm.module_version_id
            WHERE rm.release_id = p_release LOOP
    IF p_stage = 'submit' AND mv.status NOT IN ('medical_review','approved','published') THEN
      RAISE EXCEPTION 'Soumission impossible : version de module % encore en « % »', mv.id, mv.status
        USING ERRCODE = 'check_violation', HINT = 'nl:unsubmitted_component';
    END IF;
    IF p_stage IN ('approve','publish') THEN
      IF mv.status NOT IN ('approved','published') THEN
        RAISE EXCEPTION 'Version de module % non approuvée (« % ») : édition non validable', mv.id, mv.status
          USING ERRCODE = 'check_violation', HINT = 'nl:unapproved_component';
      END IF;
      IF mv.content_sha256 IS DISTINCT FROM nl_module_version_sha256(mv.id) THEN
        RAISE EXCEPTION 'Version de module % modifiée depuis son approbation', mv.id
          USING ERRCODE = 'check_violation', HINT = 'nl:tampered';
      END IF;
    END IF;
    IF p_reviewer IS NOT NULL AND mv.author_id = p_reviewer THEN
      RAISE EXCEPTION 'Indépendance : le relecteur est l''auteur de la version de module % incluse', mv.id
        USING ERRCODE = 'check_violation', HINT = 'nl:reviewer_not_independent';
    END IF;
  END LOOP;

  FOR lv IN SELECT v.* FROM nl_release_lessons(p_release) x JOIN lesson_versions v ON v.id = x.lesson_version_id LOOP
    IF lv.jurisdiction_id IS NOT NULL AND lv.jurisdiction_id IS DISTINCT FROM v_jur THEN
      RAISE EXCEPTION 'Édition incohérente : version % propre à une autre juridiction', lv.id
        USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_reference';
    END IF;
    IF p_stage IN ('approve','publish') THEN
      IF lv.status NOT IN ('approved','published') OR lv.content_sha256 IS DISTINCT FROM nl_lesson_version_sha256(lv.id) THEN
        RAISE EXCEPTION 'Version de leçon % non approuvée ou modifiée', lv.id
          USING ERRCODE = 'check_violation', HINT = 'nl:unapproved_component';
      END IF;
      IF v_prog.is_medical AND NOT nl_has_verified_source(lv.id) THEN
        RAISE EXCEPTION 'Programme médical : la version % ne cite aucune source active et vérifiée', lv.id
          USING ERRCODE = 'check_violation', HINT = 'nl:missing_source';
      END IF;
    END IF;
    IF p_reviewer IS NOT NULL AND lv.author_id = p_reviewer THEN
      RAISE EXCEPTION 'Indépendance : le relecteur est l''auteur de la version de leçon % incluse', lv.id
        USING ERRCODE = 'check_violation', HINT = 'nl:reviewer_not_independent';
    END IF;
  END LOOP;
END $$;

-- -----------------------------------------------------------------------------
-- 8. Redéfinitions des fonctions de 0005 (corps d'origine restaurés par le retour arrière)
-- -----------------------------------------------------------------------------
-- 8.1 Validation des éditions historiques (manifeste) : source active ET vérifiée.
CREATE OR REPLACE FUNCTION nl_validate_release(p_program uuid, p_manifest jsonb, p_stage text, p_reviewer uuid)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_prog    programs%ROWTYPE;
  v_jur     uuid;
  m         jsonb;
  l         jsonb;
  v         record;
  v_lv      lesson_versions%ROWTYPE;
  v_mod     uuid;
  v_less    uuid;
  v_morg    uuid;
  v_count   integer := 0;
BEGIN
  SELECT * INTO v_prog FROM programs WHERE id = p_program;
  SELECT jurisdiction_id INTO v_jur FROM professions WHERE id = v_prog.profession_id;

  IF jsonb_array_length(p_manifest -> 'modules') = 0 THEN
    RAISE EXCEPTION 'Édition vide : au moins un module est requis' USING ERRCODE = 'check_violation';
  END IF;
  -- Unicité des modules, des positions de module et des leçons dans l'édition.
  IF (SELECT count(*) <> count(DISTINCT x ->> 'module_id') OR count(*) <> count(DISTINCT x ->> 'position')
      FROM jsonb_array_elements(p_manifest -> 'modules') x) THEN
    RAISE EXCEPTION 'Édition incohérente : module ou position en double' USING ERRCODE = 'check_violation';
  END IF;
  IF (SELECT count(*) <> count(DISTINCT y ->> 'lesson_id')
      FROM jsonb_array_elements(p_manifest -> 'modules') x, jsonb_array_elements(x -> 'lessons') y) THEN
    RAISE EXCEPTION 'Édition incohérente : leçon citée deux fois' USING ERRCODE = 'check_violation';
  END IF;

  FOR m IN SELECT * FROM jsonb_array_elements(p_manifest -> 'modules') LOOP
    IF jsonb_typeof(m -> 'module_id') <> 'string' OR jsonb_typeof(m -> 'position') <> 'number'
       OR jsonb_typeof(m -> 'required') <> 'boolean' OR jsonb_typeof(m -> 'lessons') <> 'array'
       OR jsonb_array_length(m -> 'lessons') = 0 THEN
      RAISE EXCEPTION 'Édition incohérente : module mal décrit (%)', m USING ERRCODE = 'check_violation';
    END IF;
    v_mod := (m ->> 'module_id')::uuid;
    SELECT organization_id INTO v_morg FROM modules WHERE id = v_mod;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Édition incohérente : module % inconnu', v_mod USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_morg IS NOT NULL AND v_morg IS DISTINCT FROM v_prog.organization_id THEN
      RAISE EXCEPTION 'Module privé % hors de l''établissement du programme', v_mod USING ERRCODE = 'check_violation';
    END IF;
    IF (SELECT count(*) <> count(DISTINCT y ->> 'position') FROM jsonb_array_elements(m -> 'lessons') y) THEN
      RAISE EXCEPTION 'Édition incohérente : position de leçon en double dans le module %', v_mod
        USING ERRCODE = 'check_violation';
    END IF;

    FOR l IN SELECT * FROM jsonb_array_elements(m -> 'lessons') LOOP
      IF jsonb_typeof(l -> 'lesson_id') <> 'string' OR jsonb_typeof(l -> 'position') <> 'number'
         OR jsonb_typeof(l -> 'versions') <> 'object' OR NOT (l -> 'versions') ? 'fr' THEN
        RAISE EXCEPTION 'Édition incohérente : leçon mal décrite ou sans version française (%)', l
          USING ERRCODE = 'check_violation';
      END IF;
      v_less := (l ->> 'lesson_id')::uuid;
      IF NOT EXISTS (SELECT 1 FROM lessons WHERE id = v_less AND module_id = v_mod) THEN
        RAISE EXCEPTION 'Édition incohérente : leçon % absente du module %', v_less, v_mod
          USING ERRCODE = 'foreign_key_violation';
      END IF;
      FOR v IN SELECT key, value FROM jsonb_each(l -> 'versions') LOOP
        SELECT * INTO v_lv FROM lesson_versions WHERE id = (v.value #>> '{}')::uuid;
        IF NOT FOUND OR v_lv.lesson_id <> v_less THEN
          RAISE EXCEPTION 'Édition incohérente : version % n''appartient pas à la leçon %', v.value, v_less
            USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF v_lv.locale <> v.key THEN
          RAISE EXCEPTION 'Édition incohérente : version % en « % » rangée sous « % »', v_lv.id, v_lv.locale, v.key
            USING ERRCODE = 'check_violation';
        END IF;
        IF v_lv.jurisdiction_id IS NOT NULL AND v_lv.jurisdiction_id IS DISTINCT FROM v_jur THEN
          RAISE EXCEPTION 'Édition incohérente : version % propre à une autre juridiction', v_lv.id
            USING ERRCODE = 'check_violation';
        END IF;
        IF p_stage = 'submit' AND v_lv.status NOT IN ('medical_review','approved','published') THEN
          RAISE EXCEPTION 'Soumission impossible : version % encore en « % »', v_lv.id, v_lv.status
            USING ERRCODE = 'check_violation';
        END IF;
        IF p_stage IN ('approve','publish') AND v_lv.status NOT IN ('approved','published') THEN
          RAISE EXCEPTION 'Version % non approuvée (« % ») : édition non validable', v_lv.id, v_lv.status
            USING ERRCODE = 'check_violation';
        END IF;
        IF p_stage IN ('approve','publish') AND v_lv.content_sha256 IS DISTINCT FROM nl_lesson_version_sha256(v_lv.id) THEN
          RAISE EXCEPTION 'Version % modifiée depuis son approbation', v_lv.id USING ERRCODE = 'check_violation';
        END IF;
        -- Programme médical : chaque leçon s'appuie sur au moins une source active.
        IF p_stage IN ('approve','publish') AND v_prog.is_medical AND NOT nl_has_verified_source(v_lv.id) THEN
          RAISE EXCEPTION 'Programme médical : la version % ne cite aucune source active et vérifiée', v_lv.id
            USING ERRCODE = 'check_violation', HINT = 'nl:missing_source';
        END IF;
        -- Indépendance : le relecteur de l'édition n'est l'auteur d'aucune version incluse.
        IF p_reviewer IS NOT NULL AND v_lv.author_id = p_reviewer THEN
          RAISE EXCEPTION 'Indépendance : le relecteur est l''auteur de la version % incluse', v_lv.id
            USING ERRCODE = 'check_violation';
        END IF;
        v_count := v_count + 1;
      END LOOP;
    END LOOP;
  END LOOP;
  IF v_count = 0 THEN
    RAISE EXCEPTION 'Édition sans version de leçon' USING ERRCODE = 'check_violation';
  END IF;
END $$;

-- 8.2 Transitions d'édition : aiguillage selon la structure.
CREATE OR REPLACE FUNCTION nl_program_release_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid;
  v_org   uuid;
  v_sha   char(64);
BEGIN
  IF NEW.id <> OLD.id OR NEW.program_id <> OLD.program_id OR NEW.release_no <> OLD.release_no
     OR NEW.author_id <> OLD.author_id OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'Identité d''une édition non modifiable' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  SELECT organization_id INTO v_org FROM programs WHERE id = OLD.program_id;

  IF NEW.status = OLD.status THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Édition % en état « % » : modification directe interdite, créer une nouvelle édition',
        OLD.release_no, OLD.status USING ERRCODE = 'check_violation', HINT = 'nl:not_draft';
    END IF;
    IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_by, NEW.published_at, NEW.archived_at,
                    NEW.content_sha256, NEW.submitted_at) > 0 THEN
      RAISE EXCEPTION 'Un brouillon d''édition ne porte aucune trace de relecture'
        USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT nl_transition_allowed(OLD.status, NEW.status) THEN
    RAISE EXCEPTION 'Transition interdite : % → %', OLD.status, NEW.status
      USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
  END IF;
  IF NEW.manifest <> OLD.manifest OR NEW.change_summary IS DISTINCT FROM OLD.change_summary THEN
    RAISE EXCEPTION 'Une transition ne modifie pas le contenu de l''édition'
      USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF (NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
      OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256) AND NEW.status <> 'approved' THEN
    RAISE EXCEPTION 'Champs de relecture non modifiables hors approbation'
      USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
  END IF;
  IF (NEW.published_by IS DISTINCT FROM OLD.published_by OR NEW.published_at IS DISTINCT FROM OLD.published_at)
     AND NEW.status <> 'published' THEN
    RAISE EXCEPTION 'Champs de publication non modifiables hors publication'
      USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
  END IF;
  IF NEW.submitted_at IS DISTINCT FROM OLD.submitted_at AND NEW.status NOT IN ('medical_review','draft') THEN
    RAISE EXCEPTION 'Date de soumission non modifiable' USING ERRCODE = 'check_violation', HINT = 'nl:forged_review';
  END IF;

  v_actor := nl_actor();
  PERFORM nl_check_transition_actor(OLD.status, NEW.status, v_actor, v_org, OLD.author_id);

  IF NEW.status = 'medical_review' THEN
    IF OLD.structure = 'manifest_v1' THEN
      PERFORM nl_validate_release(OLD.program_id, OLD.manifest, 'submit', NULL);
    ELSE
      PERFORM nl_validate_release_v2(OLD.id, 'submit', NULL);
    END IF;
    NEW.submitted_at := now();
  ELSIF OLD.status = 'medical_review' AND NEW.status = 'draft' THEN
    IF length(btrim(coalesce(current_setting('nl.decision_comment', true), ''))) < 5 THEN
      RAISE EXCEPTION 'Demande de modifications : motif obligatoire (nl.decision_comment)'
        USING ERRCODE = 'check_violation', HINT = 'nl:comment_required';
    END IF;
    NEW.submitted_at := NULL;
  ELSIF NEW.status = 'approved' THEN
    IF OLD.structure = 'manifest_v1' THEN
      PERFORM nl_validate_release(OLD.program_id, OLD.manifest, 'approve', v_actor);
      NEW.content_sha256 := encode(digest(OLD.program_id::text || chr(31) || OLD.release_no || chr(31)
                                          || OLD.manifest::text, 'sha256'), 'hex');
    ELSE
      PERFORM nl_validate_release_v2(OLD.id, 'approve', v_actor);
      NEW.content_sha256 := nl_release_v2_sha256(OLD.id);
    END IF;
    NEW.reviewed_by := v_actor;
    NEW.reviewed_at := now();
  ELSIF NEW.status = 'published' THEN
    IF OLD.structure = 'manifest_v1' THEN
      v_sha := encode(digest(OLD.program_id::text || chr(31) || OLD.release_no || chr(31)
                             || OLD.manifest::text, 'sha256'), 'hex');
    ELSE
      v_sha := nl_release_v2_sha256(OLD.id);
    END IF;
    IF OLD.content_sha256 IS DISTINCT FROM v_sha THEN
      RAISE EXCEPTION 'Publication refusée : l''édition a changé depuis son approbation'
        USING ERRCODE = 'check_violation', HINT = 'nl:tampered';
    END IF;
    IF OLD.structure = 'manifest_v1' THEN
      PERFORM nl_validate_release(OLD.program_id, OLD.manifest, 'publish', OLD.reviewed_by);
    ELSE
      PERFORM nl_validate_release_v2(OLD.id, 'publish', OLD.reviewed_by);
    END IF;
    NEW.published_by := v_actor;
    NEW.published_at := now();
    -- L'édition publiée précédente du MÊME programme est archivée ; ses inscriptions y restent.
    UPDATE program_releases SET status = 'archived', archived_at = now()
    WHERE program_id = OLD.program_id AND status = 'published' AND id <> OLD.id;
  ELSIF NEW.status = 'archived' THEN
    NEW.archived_at := now();
  END IF;
  RETURN NEW;
END $$;

-- 8.3 Effets d'une publication : plus aucun archivage implicite ni blocage d'un autre programme.
CREATE OR REPLACE FUNCTION nl_program_release_after() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_org uuid;
BEGIN
  SELECT organization_id INTO v_org FROM programs WHERE id = NEW.program_id;
  PERFORM nl_log_decision('program_release', NEW.id, OLD.status, NEW.status, v_org, NEW.content_sha256);
  IF NEW.status = 'published' THEN
    -- Versions de modules et de leçons approuvées citées par l'édition : publiées par le même acteur.
    UPDATE module_versions SET status = 'published'
    WHERE id IN (SELECT module_version_id FROM program_release_modules WHERE release_id = NEW.id)
      AND status = 'approved';
    UPDATE lesson_versions SET status = 'published'
    WHERE id IN (SELECT x.lesson_version_id FROM nl_release_lessons(NEW.id) x) AND status = 'approved';
    UPDATE modules SET status = 'published'
    WHERE id IN (SELECT x.module_id FROM nl_release_lessons(NEW.id) x) AND status <> 'published';
    UPDATE programs SET status = 'published' WHERE id = NEW.program_id AND status <> 'published';
  ELSIF OLD.status = 'published' AND NEW.status = 'archived' THEN
    IF NOT EXISTS (SELECT 1 FROM program_releases WHERE program_id = NEW.program_id AND status = 'published') THEN
      UPDATE programs SET status = 'archived' WHERE id = NEW.program_id AND status = 'published';
    END IF;
    UPDATE modules m SET status = 'archived'
    WHERE m.id IN (SELECT x.module_id FROM nl_release_lessons(NEW.id) x)
      AND m.status = 'published'
      AND NOT EXISTS (SELECT 1 FROM program_releases r
                      WHERE r.status = 'published' AND m.id IN (SELECT y.module_id FROM nl_release_lessons(r.id) y));
  END IF;
  RETURN NULL;
END $$;

-- 8.4 Catalogue des modules.
CREATE OR REPLACE FUNCTION nl_module_catalog_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'published' AND (TG_OP = 'INSERT' OR OLD.status <> 'published')
     AND NOT EXISTS (SELECT 1 FROM program_releases r
                     WHERE r.status = 'published' AND NEW.id IN (SELECT x.module_id FROM nl_release_lessons(r.id) x)) THEN
    RAISE EXCEPTION 'Module % : publication uniquement par une édition publiée', NEW.code
      USING ERRCODE = 'check_violation', HINT = 'nl:publish_via_release';
  END IF;
  RETURN NEW;
END $$;

-- 8.5 Inscriptions : le point de reprise appartient à l'édition suivie.
CREATE OR REPLACE FUNCTION nl_enrollment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prog_org uuid;
  v_status   text;
  v_new_no   integer;
  v_old_no   integer;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Inscription non supprimable : retirer l''inscription (status = withdrawn)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT organization_id INTO v_prog_org FROM programs WHERE id = NEW.program_id;

  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'active' OR NEW.completed_at IS NOT NULL OR NEW.withdrawn_at IS NOT NULL THEN
      RAISE EXCEPTION 'Une inscription naît active' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.user_id AND status = 'active' AND deleted_at IS NULL) THEN
      RAISE EXCEPTION 'Inscription réservée à un compte actif' USING ERRCODE = 'check_violation';
    END IF;
    SELECT status INTO v_status FROM program_releases WHERE id = NEW.release_id;
    IF v_status <> 'published' THEN
      RAISE EXCEPTION 'Inscription possible seulement à l''édition publiée (édition « % »)', v_status
        USING ERRCODE = 'check_violation';
    END IF;
  ELSE
    IF NEW.user_id <> OLD.user_id OR NEW.program_id <> OLD.program_id OR NEW.enrolled_at <> OLD.enrolled_at
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
      RAISE EXCEPTION 'Titulaire, programme, établissement et date d''une inscription non modifiables'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.release_id <> OLD.release_id THEN
      SELECT status, release_no INTO v_status, v_new_no FROM program_releases WHERE id = NEW.release_id;
      SELECT release_no INTO v_old_no FROM program_releases WHERE id = OLD.release_id;
      IF v_status <> 'published' OR v_new_no <= v_old_no THEN
        RAISE EXCEPTION 'Changement d''édition : seulement vers une édition publiée plus récente'
          USING ERRCODE = 'check_violation';
      END IF;
    END IF;
    IF NEW.status <> OLD.status AND (OLD.status, NEW.status) NOT IN
         (('active','completed'), ('active','withdrawn'), ('withdrawn','active')) THEN
      RAISE EXCEPTION 'Transition d''inscription interdite : % → %', OLD.status, NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'withdrawn' AND NEW.status = 'active' THEN
      NEW.withdrawn_at := NULL;
    END IF;
  END IF;

  -- Isolation des établissements.
  IF v_prog_org IS NOT NULL AND NEW.organization_id IS DISTINCT FROM v_prog_org THEN
    RAISE EXCEPTION 'Programme d''établissement : inscription rattachée à cet établissement seulement'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.organization_id IS NOT NULL
     AND NEW.organization_id NOT IN (SELECT nl_user_organizations(NEW.user_id)) THEN
    RAISE EXCEPTION 'L''étudiant n''appartient pas à l''établissement %', NEW.organization_id
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.cohort_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM cohorts c JOIN cohort_members cm ON cm.cohort_id = c.id
       WHERE c.id = NEW.cohort_id AND c.organization_id = NEW.organization_id AND cm.user_id = NEW.user_id) THEN
    RAISE EXCEPTION 'Cohorte hors de l''établissement ou sans l''étudiant' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.last_lesson_id IS NOT NULL
     AND (TG_OP = 'INSERT' OR NEW.last_lesson_id IS DISTINCT FROM OLD.last_lesson_id OR NEW.release_id <> OLD.release_id)
     AND NOT EXISTS (SELECT 1 FROM nl_release_lessons(NEW.release_id) x WHERE x.lesson_id = NEW.last_lesson_id) THEN
    RAISE EXCEPTION 'Point de reprise hors de l''édition suivie' USING ERRCODE = 'check_violation',
      HINT = 'nl:incoherent_resume_point';
  END IF;
  RETURN NEW;
END $$;

-- 8.6 Historique : cohérence avec l'édition suivie, version de module comprise.
CREATE OR REPLACE FUNCTION nl_learning_event_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  e         enrollments%ROWTYPE;
  v_struct  text;
  v_lv      lesson_versions%ROWTYPE;
BEGIN
  SELECT * INTO e FROM enrollments WHERE id = NEW.enrollment_id;
  IF NEW.release_id <> e.release_id THEN
    RAISE EXCEPTION 'Événement incohérent : édition différente de celle suivie'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_event';
  END IF;
  IF e.status <> 'active' AND NEW.event_type NOT IN ('withdrawn','resumed','program_completed') THEN
    RAISE EXCEPTION 'Événement refusé : inscription « % »', e.status
      USING ERRCODE = 'check_violation', HINT = 'nl:inactive_enrollment';
  END IF;
  IF NEW.occurred_at < e.enrolled_at - interval '5 minutes' THEN
    RAISE EXCEPTION 'Événement antérieur à l''inscription' USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_event';
  END IF;
  SELECT structure INTO v_struct FROM program_releases WHERE id = NEW.release_id;
  IF v_struct = 'module_versions' AND NEW.module_id IS NOT NULL AND NEW.module_version_id IS NULL THEN
    RAISE EXCEPTION 'Événement incohérent : version de module obligatoire pour cette édition'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_event';
  END IF;
  IF v_struct = 'manifest_v1' AND NEW.module_version_id IS NOT NULL THEN
    RAISE EXCEPTION 'Événement incohérent : édition historique sans version de module'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_event';
  END IF;
  IF NEW.module_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM nl_release_lessons(NEW.release_id) x
       WHERE x.module_id = NEW.module_id AND x.module_version_id IS NOT DISTINCT FROM NEW.module_version_id) THEN
    RAISE EXCEPTION 'Événement incohérent : module hors de l''édition' USING ERRCODE = 'check_violation',
      HINT = 'nl:incoherent_event';
  END IF;
  IF NEW.lesson_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM nl_release_lessons(NEW.release_id) x
       WHERE x.module_id = NEW.module_id AND x.lesson_id = NEW.lesson_id
         AND (NEW.lesson_version_id IS NULL OR x.lesson_version_id = NEW.lesson_version_id)) THEN
    RAISE EXCEPTION 'Événement incohérent : leçon ou version hors de l''édition suivie'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_event';
  END IF;
  IF NEW.block_id IS NOT NULL THEN
    SELECT * INTO v_lv FROM lesson_versions WHERE id = NEW.lesson_version_id;
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_lv.body -> 'blocks') b WHERE b ->> 'id' = NEW.block_id) THEN
      RAISE EXCEPTION 'Événement incohérent : bloc % absent de la version lue', NEW.block_id
        USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_event';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- 8.7 Progression : version (de leçon et de module) issue d'une édition du programme.
CREATE OR REPLACE FUNCTION nl_student_progress_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e enrollments%ROWTYPE;
BEGIN
  IF NEW.enrollment_id IS NULL THEN
    IF NEW.module_version_id IS NOT NULL THEN
      RAISE EXCEPTION 'Progression : version de module sans inscription' USING ERRCODE = 'check_violation',
        HINT = 'nl:incoherent_progress';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO e FROM enrollments WHERE id = NEW.enrollment_id;
  IF NEW.program_id IS NOT NULL AND NEW.program_id <> e.program_id THEN
    RAISE EXCEPTION 'Progression incohérente : programme différent de l''inscription'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_progress';
  END IF;
  IF NEW.lesson_version_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM lesson_versions WHERE id = NEW.lesson_version_id AND lesson_id = NEW.lesson_id) THEN
    RAISE EXCEPTION 'Progression incohérente : version d''une autre leçon'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_progress';
  END IF;
  IF (NEW.lesson_version_id IS NOT NULL OR NEW.module_version_id IS NOT NULL) AND NOT EXISTS (
       SELECT 1 FROM program_releases r, nl_release_lessons(r.id) x
       WHERE r.program_id = e.program_id AND r.status IN ('published','archived')
         AND (NEW.lesson_version_id IS NULL OR x.lesson_version_id = NEW.lesson_version_id)
         AND (NEW.module_version_id IS NULL OR x.module_version_id = NEW.module_version_id)) THEN
    RAISE EXCEPTION 'Progression incohérente : version hors des éditions du programme'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_progress';
  END IF;
  IF NEW.last_block_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM lesson_versions lv, jsonb_array_elements(lv.body -> 'blocks') b
       WHERE lv.id = NEW.lesson_version_id AND b ->> 'id' = NEW.last_block_id) THEN
    RAISE EXCEPTION 'Progression incohérente : bloc absent de la version'
      USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_progress';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.enrollment_id IS NOT NULL AND NEW.enrollment_id IS DISTINCT FROM OLD.enrollment_id THEN
    RAISE EXCEPTION 'Progression : inscription non modifiable' USING ERRCODE = 'check_violation',
      HINT = 'nl:immutable';
  END IF;
  RETURN NEW;
END $$;

-- 8.8 Suppression d'une version de leçon : brouillon jamais cité (manifeste ou relationnel).
CREATE OR REPLACE FUNCTION nl_lesson_version_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Version % (« % ») non supprimable : historique éditorial', OLD.id, OLD.status
      USING ERRCODE = 'check_violation', HINT = 'nl:history';
  END IF;
  IF EXISTS (SELECT 1 FROM program_releases r
             WHERE r.structure = 'manifest_v1' AND OLD.id IN (SELECT nl_manifest_version_ids(r.manifest))) THEN
    RAISE EXCEPTION 'Version % citée par une édition de programme : suppression interdite', OLD.id
      USING ERRCODE = 'check_violation', HINT = 'nl:in_use';
  END IF;
  RETURN OLD;  -- les citations relationnelles sont protégées par clé étrangère (ON DELETE RESTRICT)
END $$;

-- -----------------------------------------------------------------------------
-- 9. Archivage d'une version de leçon citée par une édition publiée : refusé
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_lesson_version_in_use_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'archived' AND OLD.status <> 'archived' AND nl_lesson_version_in_published_release(OLD.id) THEN
    RAISE EXCEPTION 'Version % citée par une édition publiée : archivage refusé', OLD.id
      USING ERRCODE = 'check_violation', HINT = 'nl:in_use';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_lesson_versions_20_in_use BEFORE UPDATE OF status ON lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_in_use_guard();

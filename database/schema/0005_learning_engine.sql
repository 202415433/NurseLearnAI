-- =============================================================================
-- Migration 0005 — Moteur pédagogique : éditions, workflow éditorial, inscriptions
-- (Phase 2.2.1, docs/PHASE2.2-PLAN.md, ADR-0009). Additive et réversible.
--
--   1. Workflow éditorial strict à cinq états sur les versions de leçon et les éditions
--      de programme : draft → medical_review → approved → published → archived
--      (+ medical_review → draft « modifications demandées », draft → archived « abandon »).
--      L'ancien code « in_review » de lesson_versions devient « medical_review ».
--   2. Éditions de programme figées (program_releases) : un étudiant suit une édition
--      déterminée ; une édition soumise, approuvée ou publiée est immuable.
--   3. Journal des décisions éditoriales en ajout seul (editorial_decisions), alimenté
--      exclusivement par les déclencheurs de transition.
--   4. Inscriptions (enrollments) et historique d'apprentissage en ajout seul et idempotent
--      (learning_events), progression rattachée à l'inscription (student_progress).
--
-- Acteur d'une transition : l'application déclare l'utilisateur à l'origine de chaque
-- changement d'état dans la transaction :
--     SELECT set_config('nl.actor_id', '<uuid>', true);
--     SELECT set_config('nl.decision_comment', '<motif>', true);   -- si demandé
-- Les habilitations, l'indépendance et la portée sont vérifiées ici, en base.
-- Aucune table ni migration historique n'est réécrite ; seules des contraintes sont
-- remplacées (lesson_versions.status) et des colonnes ajoutées.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Outils
-- -----------------------------------------------------------------------------
-- Utilisateur déclaré par l'application pour la transaction en cours (obligatoire).
CREATE OR REPLACE FUNCTION nl_actor() RETURNS uuid LANGUAGE plpgsql STABLE AS $$
DECLARE v text := nullif(current_setting('nl.actor_id', true), '');
BEGIN
  IF v IS NULL THEN
    RAISE EXCEPTION 'Transition éditoriale sans acteur déclaré (nl.actor_id)' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v::uuid;
END $$;

-- Établissement de portée d'une leçon (celui de son module ; NULL = catalogue public).
CREATE OR REPLACE FUNCTION nl_lesson_org(p_lesson uuid) RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT m.organization_id FROM lessons l JOIN modules m ON m.id = l.module_id WHERE l.id = p_lesson
$$;

-- Empreinte canonique d'une version de leçon : titre, corps et sources citées.
CREATE OR REPLACE FUNCTION nl_lesson_version_sha256(p_id uuid) RETURNS char(64) LANGUAGE sql STABLE AS $$
  SELECT encode(digest(
           lv.title || chr(31) || lv.body::text || chr(31) || lv.locale || chr(31) ||
           coalesce(lv.jurisdiction_id::text, '') || chr(31) ||
           coalesce((SELECT string_agg(r.reference_id::text || '@' || coalesce(r.locator, ''), ',' ORDER BY r.reference_id)
                     FROM lesson_version_references r WHERE r.lesson_version_id = lv.id), ''),
           'sha256'), 'hex')
  FROM lesson_versions lv WHERE lv.id = p_id
$$;

-- Identifiants de toutes les versions de leçon citées par un manifeste d'édition.
CREATE OR REPLACE FUNCTION nl_manifest_version_ids(p_manifest jsonb) RETURNS SETOF uuid LANGUAGE sql IMMUTABLE AS $$
  SELECT (v.value #>> '{}')::uuid
  FROM jsonb_array_elements(p_manifest -> 'modules') m,
       jsonb_array_elements(m.value -> 'lessons') l,
       jsonb_each(l.value -> 'versions') v
$$;

-- -----------------------------------------------------------------------------
-- 1. Versions de leçon : nouveaux états et colonnes de traçabilité
-- -----------------------------------------------------------------------------
-- Compatibilité : « in_review » (lot 1) devient « medical_review ». La contrainte d'origine,
-- anonyme, est retrouvée par sa définition puis remplacée par une contrainte nommée.
DO $$
DECLARE c text;
BEGIN
  SELECT conname INTO c FROM pg_constraint
  WHERE conrelid = 'lesson_versions'::regclass AND contype = 'c'
    AND pg_get_constraintdef(oid) LIKE '%in_review%';
  IF c IS NOT NULL THEN
    EXECUTE format('ALTER TABLE lesson_versions DROP CONSTRAINT %I', c);
  END IF;
END $$;
UPDATE lesson_versions SET status = 'medical_review' WHERE status = 'in_review';
ALTER TABLE lesson_versions ADD CONSTRAINT ck_lesson_versions_status
  CHECK (status IN ('draft','medical_review','approved','published','archived'));

ALTER TABLE lesson_versions
  ADD COLUMN submitted_at    timestamptz,
  ADD COLUMN published_by    uuid REFERENCES users(id) ON DELETE RESTRICT,
  ADD COLUMN content_sha256  char(64) CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
  -- NOT VALID : contrôlées pour toute ligne nouvelle ou modifiée, sans réécrire l'historique
  -- (une version approuvée avant 0005, sans empreinte, devra être relue à nouveau).
  ADD CONSTRAINT ck_lesson_versions_approved_hash
    CHECK (status NOT IN ('approved','published') OR content_sha256 IS NOT NULL) NOT VALID,
  ADD CONSTRAINT ck_lesson_versions_published_by
    CHECK (status <> 'published' OR published_by IS NOT NULL) NOT VALID;
COMMENT ON COLUMN lesson_versions.content_sha256 IS
  'Empreinte (titre, corps, langue, juridiction, sources) figée à l''approbation et revérifiée à la publication';

-- Les programmes : nature médicale (vrai par défaut : prudence).
ALTER TABLE programs ADD COLUMN is_medical boolean NOT NULL DEFAULT true;
COMMENT ON COLUMN programs.is_medical IS
  'Programme médical : publication soumise à relecture habilitée et à des sources actives pour chaque leçon';

-- -----------------------------------------------------------------------------
-- 2. Journal des décisions éditoriales (ajout seul)
-- -----------------------------------------------------------------------------
CREATE TABLE editorial_decisions (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_type     text NOT NULL CHECK (entity_type IN ('lesson_version','program_release')),
  entity_id       uuid NOT NULL,
  from_status     text NOT NULL,
  to_status       text NOT NULL,
  decision        text NOT NULL CHECK (decision IN
                    ('submitted','changes_requested','approved','published','archived','abandoned')),
  actor_id        uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  organization_id uuid REFERENCES organizations(id) ON DELETE RESTRICT,   -- portée (NULL = catalogue public)
  content_sha256  char(64),
  comment         text CHECK (comment IS NULL OR length(comment) <= 4000),
  decided_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (from_status <> to_status),
  CHECK (decision <> 'changes_requested' OR length(btrim(coalesce(comment, ''))) >= 5)
);
CREATE INDEX ix_editorial_decisions_entity ON editorial_decisions (entity_type, entity_id, decided_at);
CREATE INDEX ix_editorial_decisions_actor ON editorial_decisions (actor_id);
COMMENT ON TABLE editorial_decisions IS
  'Décisions éditoriales, écrites uniquement par les déclencheurs de transition ; ni modification ni suppression';

CREATE OR REPLACE FUNCTION nl_editorial_decisions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('nl.editorial_log', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'editorial_decisions est alimenté uniquement par les transitions éditoriales'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_editorial_decisions_insert BEFORE INSERT ON editorial_decisions
  FOR EACH ROW EXECUTE FUNCTION nl_editorial_decisions_guard();
CREATE TRIGGER trg_editorial_decisions_no_update BEFORE UPDATE OR DELETE ON editorial_decisions
  FOR EACH ROW EXECUTE FUNCTION nl_forbid_modification();
CREATE TRIGGER trg_editorial_decisions_no_truncate BEFORE TRUNCATE ON editorial_decisions
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- Écriture d'une décision (appelée par les déclencheurs AFTER des transitions).
CREATE OR REPLACE FUNCTION nl_log_decision(p_type text, p_id uuid, p_from text, p_to text, p_org uuid, p_sha char(64))
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_decision text := CASE
    WHEN p_to = 'medical_review' THEN 'submitted'
    WHEN p_from = 'medical_review' AND p_to = 'draft' THEN 'changes_requested'
    WHEN p_to = 'approved' THEN 'approved'
    WHEN p_to = 'published' THEN 'published'
    WHEN p_to = 'archived' AND p_from = 'draft' THEN 'abandoned'
    WHEN p_to = 'archived' THEN 'archived' END;
BEGIN
  PERFORM set_config('nl.editorial_log', 'on', true);
  INSERT INTO editorial_decisions (entity_type, entity_id, from_status, to_status, decision, actor_id,
                                   organization_id, content_sha256, comment)
  VALUES (p_type, p_id, p_from, p_to, v_decision, nl_actor(), p_org, p_sha,
          nullif(btrim(coalesce(current_setting('nl.decision_comment', true), '')), ''));
  PERFORM set_config('nl.editorial_log', 'off', true);
END $$;

-- Transitions autorisées (identiques pour les versions de leçon et les éditions).
CREATE OR REPLACE FUNCTION nl_transition_allowed(p_from text, p_to text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT (p_from, p_to) IN (('draft','medical_review'), ('medical_review','draft'), ('medical_review','approved'),
                            ('approved','published'), ('approved','archived'), ('published','archived'),
                            ('draft','archived'))
$$;

-- Habilitation exigée par chaque transition (portée : établissement du contenu, ou global).
CREATE OR REPLACE FUNCTION nl_check_transition_actor(p_from text, p_to text, p_actor uuid, p_org uuid, p_author uuid)
RETURNS void LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF p_to = 'medical_review' OR (p_from = 'draft' AND p_to = 'archived') THEN
    IF NOT nl_user_has_permission(p_actor, 'content:create', p_org) THEN
      RAISE EXCEPTION 'Soumission refusée : % ne détient pas content:create dans cette portée', p_actor
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF p_to IN ('approved') OR (p_from = 'medical_review' AND p_to = 'draft') THEN
    IF NOT nl_user_has_permission(p_actor, 'content:review', p_org) THEN
      RAISE EXCEPTION 'Relecture refusée : % ne détient pas content:review dans cette portée', p_actor
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_actor = p_author THEN
      RAISE EXCEPTION 'Auto-approbation interdite : l''auteur ne peut pas relire son propre contenu'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF p_to = 'published' THEN
    IF NOT nl_user_has_permission(p_actor, 'content:publish', p_org) THEN
      RAISE EXCEPTION 'Publication refusée : % ne détient pas content:publish dans cette portée', p_actor
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF p_to = 'archived' THEN
    IF NOT (nl_user_has_permission(p_actor, 'content:review', p_org)
            OR nl_user_has_permission(p_actor, 'content:publish', p_org)) THEN
      RAISE EXCEPTION 'Archivage refusé : % n''est ni relecteur ni éditeur dans cette portée', p_actor
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 3. Workflow des versions de leçon
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_lesson_version_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_max integer;
BEGIN
  IF NEW.status <> 'draft' THEN
    RAISE EXCEPTION 'Une version de leçon naît en brouillon (reçu : %)', NEW.status USING ERRCODE = 'check_violation';
  END IF;
  IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_at, NEW.published_by,
                  NEW.content_sha256, NEW.submitted_at) > 0 THEN
    RAISE EXCEPTION 'Un brouillon ne porte aucune trace de relecture ou de publication' USING ERRCODE = 'check_violation';
  END IF;
  SELECT max(version_no) INTO v_max FROM lesson_versions
  WHERE lesson_id = NEW.lesson_id AND locale = NEW.locale AND jurisdiction_id IS NOT DISTINCT FROM NEW.jurisdiction_id;
  -- Numérotation continue : max + 1. Un numéro déjà existant est laissé à la contrainte d'unicité
  -- (ce qui garde « INSERT … ON CONFLICT DO NOTHING » idempotent pour les données de référence).
  IF NEW.version_no <> coalesce(v_max, 0) + 1 AND NOT EXISTS (
       SELECT 1 FROM lesson_versions WHERE lesson_id = NEW.lesson_id AND locale = NEW.locale
         AND jurisdiction_id IS NOT DISTINCT FROM NEW.jurisdiction_id AND version_no = NEW.version_no) THEN
    RAISE EXCEPTION 'Numéro de version attendu : % (reçu : %)', coalesce(v_max, 0) + 1, NEW.version_no
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_lesson_versions_10_insert BEFORE INSERT ON lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_insert();

CREATE OR REPLACE FUNCTION nl_lesson_version_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid;
  v_org   uuid := nl_lesson_org(OLD.lesson_id);
  v_sha   char(64);
BEGIN
  -- Identité de la version : jamais modifiable.
  IF NEW.id <> OLD.id OR NEW.lesson_id <> OLD.lesson_id OR NEW.version_no <> OLD.version_no
     OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'Identité d''une version de leçon non modifiable' USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = OLD.status THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Version % en état « % » : modification directe interdite, créer une nouvelle version',
        OLD.id, OLD.status USING ERRCODE = 'check_violation';
    END IF;
    IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_at, NEW.published_by,
                    NEW.content_sha256, NEW.submitted_at) > 0 THEN
      RAISE EXCEPTION 'Un brouillon ne porte aucune trace de relecture ou de publication' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;                                   -- édition libre d'un brouillon
  END IF;

  IF NOT nl_transition_allowed(OLD.status, NEW.status) THEN
    RAISE EXCEPTION 'Transition interdite : % → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  -- Une transition ne change que l'état (et l'échéance de révision à l'approbation).
  IF NEW.title <> OLD.title OR NEW.body <> OLD.body OR NEW.locale <> OLD.locale
     OR NEW.jurisdiction_id IS DISTINCT FROM OLD.jurisdiction_id OR NEW.author_id IS DISTINCT FROM OLD.author_id
     OR NEW.change_summary IS DISTINCT FROM OLD.change_summary
     OR (NEW.next_review_due IS DISTINCT FROM OLD.next_review_due
         AND NOT (OLD.status = 'medical_review' AND NEW.status = 'approved')) THEN
    RAISE EXCEPTION 'Une transition ne modifie pas le contenu' USING ERRCODE = 'check_violation';
  END IF;

  v_actor := nl_actor();
  PERFORM nl_check_transition_actor(OLD.status, NEW.status, v_actor, v_org, OLD.author_id);

  IF NEW.status = 'medical_review' THEN
    IF OLD.author_id IS NULL THEN
      RAISE EXCEPTION 'Soumission impossible : auteur non renseigné' USING ERRCODE = 'check_violation';
    END IF;
    IF jsonb_array_length(coalesce(OLD.body -> 'blocks', '[]'::jsonb)) = 0 THEN
      RAISE EXCEPTION 'Soumission impossible : leçon sans contenu' USING ERRCODE = 'check_violation';
    END IF;
    NEW.submitted_at := now();
  ELSIF OLD.status = 'medical_review' AND NEW.status = 'draft' THEN
    IF length(btrim(coalesce(current_setting('nl.decision_comment', true), ''))) < 5 THEN
      RAISE EXCEPTION 'Demande de modifications : motif obligatoire (nl.decision_comment)' USING ERRCODE = 'check_violation';
    END IF;
    NEW.submitted_at := NULL;
  ELSIF NEW.status = 'approved' THEN
    IF NEW.next_review_due IS NULL OR NEW.next_review_due <= current_date THEN
      RAISE EXCEPTION 'Approbation : date de prochaine révision future obligatoire' USING ERRCODE = 'check_violation';
    END IF;
    NEW.reviewed_by := v_actor;
    NEW.reviewed_at := now();
    NEW.content_sha256 := nl_lesson_version_sha256(OLD.id);
  ELSIF NEW.status = 'published' THEN
    v_sha := nl_lesson_version_sha256(OLD.id);
    IF v_sha IS DISTINCT FROM OLD.content_sha256 THEN
      RAISE EXCEPTION 'Publication refusée : le contenu a changé depuis l''approbation' USING ERRCODE = 'check_violation';
    END IF;
    NEW.published_by := v_actor;
    NEW.published_at := now();
  END IF;
  -- Champs de relecture et de publication uniquement posés par ce déclencheur.
  IF NEW.status <> 'approved' AND (NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by
       OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256) THEN
    RAISE EXCEPTION 'Champs de relecture non modifiables hors approbation' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status <> 'published' AND (NEW.published_by IS DISTINCT FROM OLD.published_by
       OR NEW.published_at IS DISTINCT FROM OLD.published_at) THEN
    RAISE EXCEPTION 'Champs de publication non modifiables hors publication' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.submitted_at IS DISTINCT FROM OLD.submitted_at AND NEW.status NOT IN ('medical_review','draft') THEN
    RAISE EXCEPTION 'Date de soumission non modifiable' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_lesson_versions_10_workflow BEFORE UPDATE ON lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_update();

CREATE OR REPLACE FUNCTION nl_lesson_version_log() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM nl_log_decision('lesson_version', NEW.id, OLD.status, NEW.status, nl_lesson_org(NEW.lesson_id),
                          coalesce(NEW.content_sha256, nl_lesson_version_sha256(NEW.id)));
  RETURN NULL;
END $$;
CREATE TRIGGER trg_lesson_versions_90_log AFTER UPDATE OF status ON lesson_versions
  FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION nl_lesson_version_log();

-- Suppression : seulement un brouillon jamais cité par une édition.
CREATE OR REPLACE FUNCTION nl_lesson_version_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Version % (« % ») non supprimable : historique éditorial', OLD.id, OLD.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM program_releases r WHERE OLD.id IN (SELECT nl_manifest_version_ids(r.manifest))) THEN
    RAISE EXCEPTION 'Version % citée par une édition de programme : suppression interdite', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN OLD;
END $$;

-- Sources d'une version : figées dès la soumission.
CREATE OR REPLACE FUNCTION nl_lesson_version_refs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM lesson_versions
  WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.lesson_version_id ELSE NEW.lesson_version_id END;
  IF v_status IS NOT NULL AND v_status <> 'draft' THEN
    RAISE EXCEPTION 'Sources d''une version « % » non modifiables', v_status USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.lesson_version_id <> OLD.lesson_version_id THEN
    SELECT status INTO v_status FROM lesson_versions WHERE id = OLD.lesson_version_id;
    IF v_status <> 'draft' THEN
      RAISE EXCEPTION 'Sources d''une version « % » non modifiables', v_status USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_lesson_version_references_guard BEFORE INSERT OR UPDATE OR DELETE ON lesson_version_references
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_refs_guard();

-- -----------------------------------------------------------------------------
-- 4. Éditions de programme
-- -----------------------------------------------------------------------------
-- manifest = {"modules":[{"module_id":uuid,"position":int,"required":bool,
--              "lessons":[{"lesson_id":uuid,"position":int,"versions":{"fr":uuid,"en":uuid}}]}]}
CREATE TABLE program_releases (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id      uuid NOT NULL REFERENCES programs(id) ON DELETE RESTRICT,
  release_no      integer NOT NULL CHECK (release_no >= 1),
  status          text NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','medical_review','approved','published','archived')),
  manifest        jsonb NOT NULL DEFAULT '{"modules":[]}'
                  CHECK (jsonb_typeof(manifest) = 'object' AND jsonb_typeof(manifest -> 'modules') = 'array'),
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
  UNIQUE (program_id, release_no),
  UNIQUE (id, program_id),                                   -- cible des clés composées (inscriptions)
  CHECK (status NOT IN ('approved','published') OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
                                                   AND content_sha256 IS NOT NULL)),
  CHECK (reviewed_by IS NULL OR reviewed_by <> author_id),
  CHECK (status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL)),
  CHECK (status <> 'archived' OR archived_at IS NOT NULL)
);
-- Au plus une édition publiée par programme.
CREATE UNIQUE INDEX ux_program_releases_published ON program_releases (program_id) WHERE status = 'published';
CREATE INDEX ix_program_releases_status ON program_releases (status);
CREATE TRIGGER trg_program_releases_updated_at BEFORE UPDATE ON program_releases
  FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at();
COMMENT ON TABLE program_releases IS
  'Édition figée d''un programme : liste ordonnée des modules, leçons et versions exactes suivies par les étudiants';

-- Validation d'un manifeste (structure, appartenance, cohérence langue × juridiction, états).
-- p_stage : 'submit' (versions au moins soumises), 'approve' / 'publish' (versions approuvées ou publiées).
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
        IF p_stage IN ('approve','publish') AND v_prog.is_medical AND NOT EXISTS (
             SELECT 1 FROM lesson_version_references r JOIN medical_references mr ON mr.id = r.reference_id
             WHERE r.lesson_version_id = v_lv.id AND mr.status = 'active') THEN
          RAISE EXCEPTION 'Programme médical : la version % ne cite aucune source active', v_lv.id
            USING ERRCODE = 'check_violation';
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

CREATE OR REPLACE FUNCTION nl_program_release_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_max integer;
BEGIN
  IF NEW.status <> 'draft' THEN
    RAISE EXCEPTION 'Une édition naît en brouillon (reçu : %)', NEW.status USING ERRCODE = 'check_violation';
  END IF;
  IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_by, NEW.published_at, NEW.archived_at,
                  NEW.content_sha256, NEW.submitted_at) > 0 THEN
    RAISE EXCEPTION 'Un brouillon d''édition ne porte aucune trace de relecture' USING ERRCODE = 'check_violation';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('nl_release_' || NEW.program_id::text));
  SELECT max(release_no) INTO v_max FROM program_releases WHERE program_id = NEW.program_id;
  IF NEW.release_no <> coalesce(v_max, 0) + 1 THEN
    RAISE EXCEPTION 'Numéro d''édition attendu : % (reçu : %)', coalesce(v_max, 0) + 1, NEW.release_no
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_program_releases_10_insert BEFORE INSERT ON program_releases
  FOR EACH ROW EXECUTE FUNCTION nl_program_release_insert();

CREATE OR REPLACE FUNCTION nl_program_release_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid;
  v_org   uuid;
BEGIN
  IF NEW.id <> OLD.id OR NEW.program_id <> OLD.program_id OR NEW.release_no <> OLD.release_no
     OR NEW.author_id <> OLD.author_id OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'Identité d''une édition non modifiable' USING ERRCODE = 'check_violation';
  END IF;
  SELECT organization_id INTO v_org FROM programs WHERE id = OLD.program_id;

  IF NEW.status = OLD.status THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Édition % en état « % » : modification directe interdite, créer une nouvelle édition',
        OLD.release_no, OLD.status USING ERRCODE = 'check_violation';
    END IF;
    IF num_nonnulls(NEW.reviewed_by, NEW.reviewed_at, NEW.published_by, NEW.published_at, NEW.archived_at,
                    NEW.content_sha256, NEW.submitted_at) > 0 THEN
      RAISE EXCEPTION 'Un brouillon d''édition ne porte aucune trace de relecture' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT nl_transition_allowed(OLD.status, NEW.status) THEN
    RAISE EXCEPTION 'Transition interdite : % → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.manifest <> OLD.manifest OR NEW.change_summary IS DISTINCT FROM OLD.change_summary THEN
    RAISE EXCEPTION 'Une transition ne modifie pas le contenu de l''édition' USING ERRCODE = 'check_violation';
  END IF;
  IF (NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
      OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256) AND NEW.status <> 'approved' THEN
    RAISE EXCEPTION 'Champs de relecture non modifiables hors approbation' USING ERRCODE = 'check_violation';
  END IF;
  IF (NEW.published_by IS DISTINCT FROM OLD.published_by OR NEW.published_at IS DISTINCT FROM OLD.published_at)
     AND NEW.status <> 'published' THEN
    RAISE EXCEPTION 'Champs de publication non modifiables hors publication' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.submitted_at IS DISTINCT FROM OLD.submitted_at AND NEW.status NOT IN ('medical_review','draft') THEN
    RAISE EXCEPTION 'Date de soumission non modifiable' USING ERRCODE = 'check_violation';
  END IF;

  v_actor := nl_actor();
  PERFORM nl_check_transition_actor(OLD.status, NEW.status, v_actor, v_org, OLD.author_id);

  IF NEW.status = 'medical_review' THEN
    PERFORM nl_validate_release(OLD.program_id, OLD.manifest, 'submit', NULL);
    NEW.submitted_at := now();
  ELSIF OLD.status = 'medical_review' AND NEW.status = 'draft' THEN
    IF length(btrim(coalesce(current_setting('nl.decision_comment', true), ''))) < 5 THEN
      RAISE EXCEPTION 'Demande de modifications : motif obligatoire (nl.decision_comment)' USING ERRCODE = 'check_violation';
    END IF;
    NEW.submitted_at := NULL;
  ELSIF NEW.status = 'approved' THEN
    PERFORM nl_validate_release(OLD.program_id, OLD.manifest, 'approve', v_actor);
    NEW.reviewed_by := v_actor;
    NEW.reviewed_at := now();
    NEW.content_sha256 := encode(digest(OLD.program_id::text || chr(31) || OLD.release_no || chr(31)
                                        || OLD.manifest::text, 'sha256'), 'hex');
  ELSIF NEW.status = 'published' THEN
    IF OLD.content_sha256 IS DISTINCT FROM encode(digest(OLD.program_id::text || chr(31) || OLD.release_no
                                                         || chr(31) || OLD.manifest::text, 'sha256'), 'hex') THEN
      RAISE EXCEPTION 'Publication refusée : l''édition a changé depuis son approbation' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM nl_validate_release(OLD.program_id, OLD.manifest, 'publish', OLD.reviewed_by);
    NEW.published_by := v_actor;
    NEW.published_at := now();
    -- L'édition publiée précédente est archivée (elle reste la référence des inscriptions existantes).
    UPDATE program_releases SET status = 'archived', archived_at = now()
    WHERE program_id = OLD.program_id AND status = 'published' AND id <> OLD.id;
  ELSIF NEW.status = 'archived' THEN
    NEW.archived_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_program_releases_20_workflow BEFORE UPDATE ON program_releases
  FOR EACH ROW EXECUTE FUNCTION nl_program_release_update();

-- Effets d'une transition d'édition : journal, publication des versions, visibilité du catalogue.
CREATE OR REPLACE FUNCTION nl_program_release_after() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_org uuid;
BEGIN
  SELECT organization_id INTO v_org FROM programs WHERE id = NEW.program_id;
  PERFORM nl_log_decision('program_release', NEW.id, OLD.status, NEW.status, v_org, NEW.content_sha256);
  IF NEW.status = 'published' THEN
    -- Une seule version publiée par leçon × langue × juridiction (index ux_lesson_versions_one_published, lot 1) :
    -- les versions remplacées par cette édition sont archivées, sauf si une autre édition publiée
    -- les cite encore (module partagé) — la publication est alors refusée plutôt que de laisser
    -- une édition publiée pointer vers une version archivée.
    IF EXISTS (
         SELECT 1 FROM lesson_versions old_v
         JOIN lesson_versions new_v ON new_v.lesson_id = old_v.lesson_id AND new_v.locale = old_v.locale
              AND new_v.jurisdiction_id IS NOT DISTINCT FROM old_v.jurisdiction_id
         JOIN program_releases other ON other.status = 'published' AND other.id <> NEW.id
              AND old_v.id IN (SELECT nl_manifest_version_ids(other.manifest))
         WHERE new_v.id IN (SELECT nl_manifest_version_ids(NEW.manifest)) AND new_v.id <> old_v.id
           AND old_v.status = 'published') THEN
      RAISE EXCEPTION 'Publication refusée : une version remplacée est encore suivie par une autre édition publiée'
        USING ERRCODE = 'check_violation';
    END IF;
    UPDATE lesson_versions old_v SET status = 'archived'
    FROM lesson_versions new_v
    WHERE new_v.id IN (SELECT nl_manifest_version_ids(NEW.manifest)) AND new_v.status = 'approved'
      AND old_v.lesson_id = new_v.lesson_id AND old_v.locale = new_v.locale
      AND old_v.jurisdiction_id IS NOT DISTINCT FROM new_v.jurisdiction_id
      AND old_v.status = 'published' AND old_v.id <> new_v.id;
    -- Les versions approuvées de l'édition sont publiées par le même acteur (habilitation revérifiée).
    UPDATE lesson_versions SET status = 'published'
    WHERE id IN (SELECT nl_manifest_version_ids(NEW.manifest)) AND status = 'approved';
    UPDATE modules SET status = 'published'
    WHERE id IN (SELECT (x ->> 'module_id')::uuid FROM jsonb_array_elements(NEW.manifest -> 'modules') x)
      AND status <> 'published';
    UPDATE programs SET status = 'published' WHERE id = NEW.program_id AND status <> 'published';
  ELSIF OLD.status = 'published' AND NEW.status = 'archived' THEN
    IF NOT EXISTS (SELECT 1 FROM program_releases WHERE program_id = NEW.program_id AND status = 'published') THEN
      UPDATE programs SET status = 'archived' WHERE id = NEW.program_id AND status = 'published';
    END IF;
    -- Un module qui ne figure plus dans aucune édition publiée sort du catalogue.
    UPDATE modules m SET status = 'archived'
    WHERE m.id IN (SELECT (x ->> 'module_id')::uuid FROM jsonb_array_elements(NEW.manifest -> 'modules') x)
      AND m.status = 'published'
      AND NOT EXISTS (SELECT 1 FROM program_releases r, jsonb_array_elements(r.manifest -> 'modules') y
                      WHERE r.status = 'published' AND (y ->> 'module_id')::uuid = m.id);
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER trg_program_releases_90_after AFTER UPDATE OF status ON program_releases
  FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION nl_program_release_after();

CREATE OR REPLACE FUNCTION nl_program_release_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Édition « % » non supprimable : historique éditorial', OLD.status USING ERRCODE = 'check_violation';
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER trg_program_releases_delete BEFORE DELETE ON program_releases
  FOR EACH ROW EXECUTE FUNCTION nl_program_release_delete();

-- Suppression des versions (déclarée ici : la fonction consulte program_releases).
CREATE TRIGGER trg_lesson_versions_delete BEFORE DELETE ON lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_delete();

-- -----------------------------------------------------------------------------
-- 5. Visibilité du catalogue : « published » seulement par une édition publiée
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_program_catalog_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'published' AND (TG_OP = 'INSERT' OR OLD.status <> 'published')
     AND NOT EXISTS (SELECT 1 FROM program_releases WHERE program_id = NEW.id AND status = 'published') THEN
    RAISE EXCEPTION 'Programme % : publication uniquement par une édition publiée', NEW.code
      USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND (NEW.is_medical IS DISTINCT FROM OLD.is_medical
                           OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
                           OR NEW.profession_id IS DISTINCT FROM OLD.profession_id)
     AND EXISTS (SELECT 1 FROM program_releases WHERE program_id = NEW.id AND status <> 'draft') THEN
    RAISE EXCEPTION 'Programme % : nature, établissement et métier figés dès la première édition soumise', NEW.code
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_programs_catalog_guard BEFORE INSERT OR UPDATE ON programs
  FOR EACH ROW EXECUTE FUNCTION nl_program_catalog_guard();

CREATE OR REPLACE FUNCTION nl_module_catalog_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'published' AND (TG_OP = 'INSERT' OR OLD.status <> 'published')
     AND NOT EXISTS (SELECT 1 FROM program_releases r, jsonb_array_elements(r.manifest -> 'modules') x
                     WHERE r.status = 'published' AND (x ->> 'module_id')::uuid = NEW.id) THEN
    RAISE EXCEPTION 'Module % : publication uniquement par une édition publiée', NEW.code
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_modules_catalog_guard BEFORE INSERT OR UPDATE OF status ON modules
  FOR EACH ROW EXECUTE FUNCTION nl_module_catalog_guard();

-- -----------------------------------------------------------------------------
-- 6. Inscriptions
-- -----------------------------------------------------------------------------
CREATE TABLE enrollments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  program_id       uuid NOT NULL REFERENCES programs(id) ON DELETE RESTRICT,
  release_id       uuid NOT NULL,
  organization_id  uuid REFERENCES organizations(id) ON DELETE RESTRICT,   -- établissement de rattachement
  cohort_id        uuid REFERENCES cohorts(id) ON DELETE RESTRICT,
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','withdrawn')),
  enrolled_at      timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz,
  withdrawn_at     timestamptz,
  last_activity_at timestamptz NOT NULL DEFAULT now(),
  last_lesson_id   uuid REFERENCES lessons(id) ON DELETE RESTRICT,
  last_block_id    text CHECK (last_block_id IS NULL OR length(last_block_id) BETWEEN 1 AND 64),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, program_id),
  UNIQUE (id, user_id),                                      -- cible des clés composées
  FOREIGN KEY (release_id, program_id) REFERENCES program_releases (id, program_id) ON DELETE RESTRICT,
  CHECK (cohort_id IS NULL OR organization_id IS NOT NULL),
  CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
  CHECK ((status = 'withdrawn') = (withdrawn_at IS NOT NULL))
);
CREATE INDEX ix_enrollments_release ON enrollments (release_id);
CREATE INDEX ix_enrollments_org ON enrollments (organization_id) WHERE organization_id IS NOT NULL;
CREATE INDEX ix_enrollments_cohort ON enrollments (cohort_id) WHERE cohort_id IS NOT NULL;
CREATE TRIGGER trg_enrollments_updated_at BEFORE UPDATE ON enrollments
  FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at();
COMMENT ON TABLE enrollments IS
  'Inscription d''un étudiant à un programme, rattachée à l''édition suivie (jamais supprimée)';

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
     AND NOT EXISTS (SELECT 1 FROM program_releases r, jsonb_array_elements(r.manifest -> 'modules') x,
                            jsonb_array_elements(x -> 'lessons') y
                     WHERE r.id = NEW.release_id AND (y ->> 'lesson_id')::uuid = NEW.last_lesson_id) THEN
    RAISE EXCEPTION 'Point de reprise hors de l''édition suivie' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_enrollments_guard BEFORE INSERT OR UPDATE OR DELETE ON enrollments
  FOR EACH ROW EXECUTE FUNCTION nl_enrollment_guard();

-- -----------------------------------------------------------------------------
-- 7. Historique d'apprentissage (ajout seul, idempotent)
-- -----------------------------------------------------------------------------
-- Idempotence : chaque événement porte un identifiant généré par le client (UUID) ;
-- l'API insère avec ON CONFLICT (enrollment_id, client_event_id) DO NOTHING : un renvoi
-- (réseau instable, synchronisation hors ligne) n'est jamais compté deux fois.
CREATE TABLE learning_events (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  enrollment_id      uuid NOT NULL,
  user_id            uuid NOT NULL,
  release_id         uuid NOT NULL REFERENCES program_releases(id) ON DELETE RESTRICT,
  client_event_id    uuid NOT NULL,
  event_type         text NOT NULL CHECK (event_type IN
                       ('enrolled','lesson_opened','block_reached','lesson_completed','module_completed',
                        'program_completed','release_switched','withdrawn','resumed')),
  module_id          uuid REFERENCES modules(id) ON DELETE RESTRICT,
  lesson_id          uuid REFERENCES lessons(id) ON DELETE RESTRICT,
  lesson_version_id  uuid REFERENCES lesson_versions(id) ON DELETE RESTRICT,
  block_id           text CHECK (block_id IS NULL OR length(block_id) BETWEEN 1 AND 64),
  occurred_at        timestamptz NOT NULL,
  received_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  payload            jsonb NOT NULL DEFAULT '{}'
                     CHECK (jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 2048),
  FOREIGN KEY (enrollment_id, user_id) REFERENCES enrollments (id, user_id) ON DELETE RESTRICT,
  UNIQUE (enrollment_id, client_event_id),
  CHECK (occurred_at <= received_at + interval '5 minutes'),
  CHECK (event_type NOT IN ('lesson_opened','block_reached','lesson_completed')
         OR (lesson_id IS NOT NULL AND lesson_version_id IS NOT NULL AND module_id IS NOT NULL)),
  CHECK (event_type <> 'block_reached' OR block_id IS NOT NULL),
  CHECK (event_type <> 'module_completed' OR module_id IS NOT NULL)
);
CREATE INDEX ix_learning_events_enrollment ON learning_events (enrollment_id, occurred_at);
CREATE INDEX ix_learning_events_user ON learning_events (user_id, occurred_at);
COMMENT ON TABLE learning_events IS
  'Historique d''apprentissage en ajout seul ; idempotence par (enrollment_id, client_event_id)';

CREATE OR REPLACE FUNCTION nl_learning_event_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  e        enrollments%ROWTYPE;
  v_manif  jsonb;
  v_lv     lesson_versions%ROWTYPE;
BEGIN
  SELECT * INTO e FROM enrollments WHERE id = NEW.enrollment_id;
  IF NEW.release_id <> e.release_id THEN
    RAISE EXCEPTION 'Événement incohérent : édition différente de celle suivie' USING ERRCODE = 'check_violation';
  END IF;
  IF e.status <> 'active' AND NEW.event_type NOT IN ('withdrawn','resumed','program_completed') THEN
    RAISE EXCEPTION 'Événement refusé : inscription « % »', e.status USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.occurred_at < e.enrolled_at - interval '5 minutes' THEN
    RAISE EXCEPTION 'Événement antérieur à l''inscription' USING ERRCODE = 'check_violation';
  END IF;
  SELECT manifest INTO v_manif FROM program_releases WHERE id = NEW.release_id;
  IF NEW.module_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM jsonb_array_elements(v_manif -> 'modules') x WHERE (x ->> 'module_id')::uuid = NEW.module_id) THEN
    RAISE EXCEPTION 'Événement incohérent : module hors de l''édition' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.lesson_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM jsonb_array_elements(v_manif -> 'modules') x, jsonb_array_elements(x -> 'lessons') y
       WHERE (x ->> 'module_id')::uuid = NEW.module_id AND (y ->> 'lesson_id')::uuid = NEW.lesson_id
         AND (NEW.lesson_version_id IS NULL
              OR NEW.lesson_version_id::text IN (SELECT value #>> '{}' FROM jsonb_each(y -> 'versions')))) THEN
    RAISE EXCEPTION 'Événement incohérent : leçon ou version hors de l''édition suivie' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.block_id IS NOT NULL THEN
    SELECT * INTO v_lv FROM lesson_versions WHERE id = NEW.lesson_version_id;
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_lv.body -> 'blocks') b WHERE b ->> 'id' = NEW.block_id) THEN
      RAISE EXCEPTION 'Événement incohérent : bloc % absent de la version lue', NEW.block_id
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_learning_events_check BEFORE INSERT ON learning_events
  FOR EACH ROW EXECUTE FUNCTION nl_learning_event_check();
CREATE TRIGGER trg_learning_events_no_update BEFORE UPDATE OR DELETE ON learning_events
  FOR EACH ROW EXECUTE FUNCTION nl_forbid_modification();
CREATE TRIGGER trg_learning_events_no_truncate BEFORE TRUNCATE ON learning_events
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- -----------------------------------------------------------------------------
-- 8. Progression rattachée à l'inscription
-- -----------------------------------------------------------------------------
ALTER TABLE student_progress
  ADD COLUMN enrollment_id     uuid,
  ADD COLUMN lesson_version_id uuid REFERENCES lesson_versions(id) ON DELETE RESTRICT,
  ADD COLUMN last_block_id     text CHECK (last_block_id IS NULL OR length(last_block_id) BETWEEN 1 AND 64),
  ADD CONSTRAINT fk_student_progress_enrollment
    FOREIGN KEY (enrollment_id, user_id) REFERENCES enrollments (id, user_id) ON DELETE RESTRICT,
  ADD CONSTRAINT ck_student_progress_version
    CHECK (lesson_version_id IS NULL OR lesson_id IS NOT NULL);
CREATE UNIQUE INDEX ux_student_progress_enrollment_lesson ON student_progress (enrollment_id, lesson_id)
  WHERE enrollment_id IS NOT NULL AND lesson_id IS NOT NULL;
CREATE UNIQUE INDEX ux_student_progress_enrollment_module ON student_progress (enrollment_id, module_id)
  WHERE enrollment_id IS NOT NULL AND module_id IS NOT NULL;
CREATE UNIQUE INDEX ux_student_progress_enrollment_program ON student_progress (enrollment_id, program_id)
  WHERE enrollment_id IS NOT NULL AND program_id IS NOT NULL;

CREATE OR REPLACE FUNCTION nl_student_progress_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e enrollments%ROWTYPE;
BEGIN
  IF NEW.enrollment_id IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT * INTO e FROM enrollments WHERE id = NEW.enrollment_id;
  IF NEW.program_id IS NOT NULL AND NEW.program_id <> e.program_id THEN
    RAISE EXCEPTION 'Progression incohérente : programme différent de l''inscription' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.lesson_version_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM lesson_versions WHERE id = NEW.lesson_version_id AND lesson_id = NEW.lesson_id) THEN
    RAISE EXCEPTION 'Progression incohérente : version d''une autre leçon' USING ERRCODE = 'check_violation';
  END IF;
  -- La version conservée doit appartenir à une édition du programme (l'actuelle ou une précédente).
  IF NEW.lesson_version_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM program_releases r
       WHERE r.program_id = e.program_id AND r.status IN ('published','archived')
         AND NEW.lesson_version_id IN (SELECT nl_manifest_version_ids(r.manifest))) THEN
    RAISE EXCEPTION 'Progression incohérente : version hors des éditions du programme' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.last_block_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM lesson_versions lv, jsonb_array_elements(lv.body -> 'blocks') b
       WHERE lv.id = NEW.lesson_version_id AND b ->> 'id' = NEW.last_block_id) THEN
    RAISE EXCEPTION 'Progression incohérente : bloc absent de la version' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.enrollment_id IS NOT NULL AND NEW.enrollment_id IS DISTINCT FROM OLD.enrollment_id THEN
    RAISE EXCEPTION 'Progression : inscription non modifiable' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_student_progress_check BEFORE INSERT OR UPDATE ON student_progress
  FOR EACH ROW EXECUTE FUNCTION nl_student_progress_check();

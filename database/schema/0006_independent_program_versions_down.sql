-- =============================================================================
-- Retour arrière de la migration 0006 (versions de modules indépendantes, ADR-0010).
-- Restaure exactement le schéma 0005 lorsque c'est sûr ; sinon s'ARRÊTE avec un message
-- explicite : aucune donnée n'est jamais supprimée silencieusement.
-- Situations refusées : versions de modules, éditions relationnelles, décisions sur des
-- versions de modules, événements ou progression rattachés à une version de module,
-- sources vérifiées (verified_by), plusieurs versions publiées d'une même leçon × langue ×
-- juridiction (incompatibles avec l'index unique rétabli).
-- =============================================================================
DO $$
DECLARE
  n_mv   bigint := (SELECT count(*) FROM module_versions);
  n_rel  bigint := (SELECT count(*) FROM program_releases WHERE structure = 'module_versions');
  n_dec  bigint := (SELECT count(*) FROM editorial_decisions WHERE entity_type = 'module_version');
  n_evt  bigint := (SELECT count(*) FROM learning_events WHERE module_version_id IS NOT NULL);
  n_prg  bigint := (SELECT count(*) FROM student_progress WHERE module_version_id IS NOT NULL);
  n_src  bigint := (SELECT count(*) FROM medical_references WHERE verified_by IS NOT NULL);
  n_dup  bigint := (SELECT count(*) FROM (SELECT 1 FROM lesson_versions WHERE status = 'published'
                    GROUP BY lesson_id, locale, jurisdiction_id HAVING count(*) > 1) d);
BEGIN
  IF n_mv + n_rel + n_dec + n_evt + n_prg + n_src + n_dup > 0 THEN
    RAISE EXCEPTION 'Retour arrière 0006 refusé : données propres à 0006 présentes (versions de modules=%, éditions relationnelles=%, décisions=%, événements=%, progression=%, sources vérifiées=%, leçons à plusieurs versions publiées=%). Aucune donnée n''a été modifiée.',
      n_mv, n_rel, n_dec, n_evt, n_prg, n_src, n_dup
      USING ERRCODE = 'object_in_use', HINT = 'nl:downgrade_unsafe';
  END IF;
END $$;

DROP TRIGGER IF EXISTS trg_lesson_versions_20_in_use ON lesson_versions;
DROP FUNCTION IF EXISTS nl_lesson_version_in_use_guard();

-- Corps d'origine (0005) des fonctions redéfinies par 0006.
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

DROP TRIGGER IF EXISTS trg_program_release_prerequisites_guard ON program_release_prerequisites;
DROP TRIGGER IF EXISTS trg_program_release_modules_guard ON program_release_modules;
DROP TRIGGER IF EXISTS trg_program_releases_05_structure ON program_releases;
DROP FUNCTION IF EXISTS nl_release_items_guard();
DROP FUNCTION IF EXISTS nl_program_release_structure_guard();
DROP FUNCTION IF EXISTS nl_validate_release_v2(uuid, text, uuid);

ALTER TABLE student_progress DROP COLUMN IF EXISTS module_version_id;
DROP INDEX IF EXISTS ix_learning_events_module_version;
ALTER TABLE learning_events DROP COLUMN IF EXISTS module_version_id;

DROP TABLE IF EXISTS program_release_prerequisites;
DROP TABLE IF EXISTS program_release_modules;
ALTER TABLE program_releases DROP COLUMN IF EXISTS structure;

DROP TABLE IF EXISTS module_version_lesson_versions;
DROP TABLE IF EXISTS module_version_lessons;
DROP TABLE IF EXISTS module_versions;
DROP FUNCTION IF EXISTS nl_module_version_items_guard();
DROP FUNCTION IF EXISTS nl_module_version_delete();
DROP FUNCTION IF EXISTS nl_module_version_log();
DROP FUNCTION IF EXISTS nl_module_version_update();
DROP FUNCTION IF EXISTS nl_module_version_insert();
DROP FUNCTION IF EXISTS nl_validate_module_version(uuid, text, uuid);
DROP FUNCTION IF EXISTS nl_release_v2_sha256(uuid);
DROP FUNCTION IF EXISTS nl_module_version_sha256(uuid);
DROP FUNCTION IF EXISTS nl_module_version_in_published_release(uuid);
DROP FUNCTION IF EXISTS nl_lesson_version_in_published_release(uuid);
DROP FUNCTION IF EXISTS nl_release_lessons(uuid);

DROP TRIGGER IF EXISTS trg_medical_references_verification ON medical_references;
DROP FUNCTION IF EXISTS nl_reference_verification();
DROP FUNCTION IF EXISTS nl_has_verified_source(uuid);
ALTER TABLE medical_references DROP COLUMN IF EXISTS verified_by;

DROP INDEX IF EXISTS ix_lesson_versions_published;
CREATE UNIQUE INDEX ux_lesson_versions_one_published
  ON lesson_versions (lesson_id, locale, jurisdiction_id) NULLS NOT DISTINCT
  WHERE status = 'published';

ALTER TABLE editorial_decisions DROP CONSTRAINT IF EXISTS ck_editorial_decisions_entity_type;
ALTER TABLE editorial_decisions ADD CONSTRAINT editorial_decisions_entity_type_check
  CHECK (entity_type IN ('lesson_version','program_release'));

ALTER TABLE lesson_versions DROP CONSTRAINT IF EXISTS uq_lesson_versions_id_lesson_locale;
ALTER TABLE lessons DROP CONSTRAINT IF EXISTS uq_lessons_id_module;

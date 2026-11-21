-- Données de DÉMONSTRATION pour les tests de bout en bout (CI uniquement).
-- Contenu FICTIF et NON MÉDICAL : il sert seulement à éprouver l'affichage d'un programme publié.
-- Ne jamais charger en développement ni en production : le script refuse de s'exécuter sans
--   PGOPTIONS="-c nl.allow_demo=on"
-- Mené par le workflow éditorial (0005) avec version de module explicite (0006) : brouillon → relecture
-- → approbation → publication.
-- Idempotent. Rattaché à Ontario / PSW afin que le pilote PAB Québec (brouillon) reste vide à l'écran.
DO $$
DECLARE
  v_author uuid; v_reviewer uuid; v_prof uuid; v_spec uuid; v_prog uuid; v_mod uuid; v_lesson uuid;
  v_fr uuid; v_en uuid; v_release uuid; v_mv uuid;
BEGIN
  IF current_setting('nl.allow_demo', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'Fixture de démonstration refusée : définir nl.allow_demo=on (CI uniquement).';
  END IF;

  INSERT INTO users (email, status, email_verified_at) VALUES ('e2e-author@example.test', 'active', now())
    ON CONFLICT DO NOTHING;
  INSERT INTO users (email, status, email_verified_at) VALUES ('e2e-reviewer@example.test', 'active', now())
    ON CONFLICT DO NOTHING;
  SELECT id INTO v_author FROM users WHERE email = 'e2e-author@example.test';
  SELECT id INTO v_reviewer FROM users WHERE email = 'e2e-reviewer@example.test';
  -- Donnée de démonstration : garde d'attribution des rôles contournée pour cette transaction.
  PERFORM set_config('nl.role_grant_bypass', 'on', true);
  INSERT INTO user_roles (user_id, role_id)
    SELECT v_reviewer, r.id FROM roles r
    WHERE r.code = 'medical_reviewer'
      AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = v_reviewer AND ur.role_id = r.id);

  -- Depuis 0005 : l'auteur détient content:create, et tout passe par le workflow éditorial.
  INSERT INTO user_roles (user_id, role_id)
    SELECT v_author, r.id FROM roles r
    WHERE r.code = 'instructor'
      AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = v_author AND ur.role_id = r.id);

  SELECT p.id INTO v_prof FROM professions p JOIN jurisdictions j ON j.id = p.jurisdiction_id
    WHERE j.code = 'CA-ON' AND p.code = 'psw';
  SELECT id INTO v_spec FROM specialties WHERE code = 'care_assistant_core';

  IF EXISTS (SELECT 1 FROM programs WHERE code = 'e2e-demo-program') THEN
    RETURN;                                            -- déjà chargé : idempotent
  END IF;

  -- Programme NON médical (is_medical = false) : fictif, réservé aux tests automatisés.
  INSERT INTO programs (code, profession_id, specialty_id, title_i18n, summary_i18n, level, access_tier, is_medical)
  VALUES ('e2e-demo-program', v_prof, v_spec,
          '{"fr":"Programme de démonstration (test e2e)","en":"Demo program (e2e test)"}',
          '{"fr":"Contenu fictif, non médical, réservé aux tests automatisés.","en":"Fictitious, non-medical content for automated tests only."}',
          'beginner', 'free', false)
  RETURNING id INTO v_prog;

  INSERT INTO modules (code, specialty_id, level, title_i18n, estimated_minutes, access_tier)
  VALUES ('e2e-demo-module', v_spec, 'beginner',
          '{"fr":"Module de démonstration (test e2e)","en":"Demo module (e2e test)"}', 10, 'free')
  RETURNING id INTO v_mod;
  INSERT INTO program_modules (program_id, module_id, position) VALUES (v_prog, v_mod, 0);
  INSERT INTO lessons (module_id, code, position, kind) VALUES (v_mod, 'e2e-demo-lesson', 0, 'theory')
  RETURNING id INTO v_lesson;

  INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, author_id)
  VALUES (v_lesson, 'fr', 1, 'Leçon de démonstration (test e2e)',
          '{"blocks":[{"id":"b1","type":"paragraph","text":"Contenu fictif non médical."}]}', v_author)
  RETURNING id INTO v_fr;
  INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, author_id)
  VALUES (v_lesson, 'en', 1, 'Demo lesson (e2e test)',
          '{"blocks":[{"id":"b1","type":"paragraph","text":"Fictitious, non-medical content."}]}', v_author)
  RETURNING id INTO v_en;

  -- Soumission par l'auteur, approbation et publication par le relecteur (distinct, habilité).
  PERFORM set_config('nl.actor_id', v_author::text, true);
  UPDATE lesson_versions SET status = 'medical_review' WHERE id IN (v_fr, v_en);
  PERFORM set_config('nl.actor_id', v_reviewer::text, true);
  UPDATE lesson_versions SET status = 'approved', next_review_due = current_date + 365 WHERE id IN (v_fr, v_en);

  -- Version de module (0006, ADR-0010) : leçon et versions exactes citées explicitement.
  INSERT INTO module_versions (module_id, version_no, author_id, change_summary)
  VALUES (v_mod, 1, v_author, 'Version de démonstration (CI)') RETURNING id INTO v_mv;
  INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position)
  VALUES (v_mv, v_mod, v_lesson, 0);
  INSERT INTO module_version_lesson_versions (module_version_id, lesson_id, locale, lesson_version_id)
  VALUES (v_mv, v_lesson, 'fr', v_fr), (v_mv, v_lesson, 'en', v_en);
  PERFORM set_config('nl.actor_id', v_author::text, true);
  UPDATE module_versions SET status = 'medical_review' WHERE id = v_mv;
  PERFORM set_config('nl.actor_id', v_reviewer::text, true);
  UPDATE module_versions SET status = 'approved' WHERE id = v_mv;

  INSERT INTO program_releases (program_id, release_no, author_id, change_summary)
  VALUES (v_prog, 1, v_author, 'Édition de démonstration (CI)')
  RETURNING id INTO v_release;
  INSERT INTO program_release_modules (release_id, module_id, module_version_id, position)
  VALUES (v_release, v_mod, v_mv, 0);
  PERFORM set_config('nl.actor_id', v_author::text, true);
  UPDATE program_releases SET status = 'medical_review' WHERE id = v_release;
  PERFORM set_config('nl.actor_id', v_reviewer::text, true);
  UPDATE program_releases SET status = 'approved' WHERE id = v_release;
  UPDATE program_releases SET status = 'published' WHERE id = v_release;
END $$;

-- Données de DÉMONSTRATION pour les tests de bout en bout (CI uniquement).
-- Contenu FICTIF et NON MÉDICAL : il sert seulement à éprouver l'affichage d'un programme publié.
-- Ne jamais charger en développement ni en production : le script refuse de s'exécuter sans
--   PGOPTIONS="-c nl.allow_demo=on"
-- Idempotent. Rattaché à Ontario / PSW afin que le pilote PAB Québec (brouillon) reste vide à l'écran.
DO $$
DECLARE
  v_author uuid; v_reviewer uuid; v_prof uuid; v_spec uuid; v_prog uuid; v_mod uuid; v_lesson uuid;
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
  INSERT INTO user_roles (user_id, role_id)
    SELECT v_reviewer, r.id FROM roles r
    WHERE r.code = 'medical_reviewer'
      AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = v_reviewer AND ur.role_id = r.id);

  SELECT p.id INTO v_prof FROM professions p JOIN jurisdictions j ON j.id = p.jurisdiction_id
    WHERE j.code = 'CA-ON' AND p.code = 'psw';
  SELECT id INTO v_spec FROM specialties WHERE code = 'care_assistant_core';

  IF NOT EXISTS (SELECT 1 FROM programs WHERE code = 'e2e-demo-program') THEN
    INSERT INTO programs (code, profession_id, specialty_id, title_i18n, summary_i18n, level, access_tier, status)
    VALUES ('e2e-demo-program', v_prof, v_spec,
            '{"fr":"Programme de démonstration (test e2e)","en":"Demo program (e2e test)"}',
            '{"fr":"Contenu fictif, non médical, réservé aux tests automatisés.","en":"Fictitious, non-medical content for automated tests only."}',
            'beginner', 'free', 'published');
  END IF;
  SELECT id INTO v_prog FROM programs WHERE code = 'e2e-demo-program';

  IF NOT EXISTS (SELECT 1 FROM modules WHERE code = 'e2e-demo-module') THEN
    INSERT INTO modules (code, specialty_id, level, title_i18n, estimated_minutes, access_tier, status)
    VALUES ('e2e-demo-module', v_spec, 'beginner',
            '{"fr":"Module de démonstration (test e2e)","en":"Demo module (e2e test)"}', 10, 'free', 'published');
  END IF;
  SELECT id INTO v_mod FROM modules WHERE code = 'e2e-demo-module';

  IF NOT EXISTS (SELECT 1 FROM program_modules WHERE program_id = v_prog AND module_id = v_mod) THEN
    INSERT INTO program_modules (program_id, module_id, position) VALUES (v_prog, v_mod, 0);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM lessons WHERE module_id = v_mod AND code = 'e2e-demo-lesson') THEN
    INSERT INTO lessons (module_id, code, position, kind) VALUES (v_mod, 'e2e-demo-lesson', 0, 'theory');
  END IF;
  SELECT id INTO v_lesson FROM lessons WHERE module_id = v_mod AND code = 'e2e-demo-lesson';

  IF NOT EXISTS (SELECT 1 FROM lesson_versions WHERE lesson_id = v_lesson) THEN
    INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status,
                                 author_id, reviewed_by, reviewed_at, published_at, next_review_due)
    VALUES (v_lesson, 'fr', 1, 'Leçon de démonstration (test e2e)', '{"blocks":[]}', 'published',
            v_author, v_reviewer, now(), now(), current_date + 365),
           (v_lesson, 'en', 1, 'Demo lesson (e2e test)', '{"blocks":[]}', 'published',
            v_author, v_reviewer, now(), now(), current_date + 365);
  END IF;
END $$;

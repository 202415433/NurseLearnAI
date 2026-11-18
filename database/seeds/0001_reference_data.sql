-- =============================================================================
-- Données de référence initiales (idempotentes).
-- IMPORTANT : les métiers et organismes de réglementation ci-dessous sont des
-- données de départ marquées "pending_expert_review". Ils doivent être validés
-- par un professionnel qualifié de chaque juridiction avant publication.
-- =============================================================================

INSERT INTO locales (code, name) VALUES
  ('fr', 'Français'), ('en', 'English')
ON CONFLICT (code) DO NOTHING;

INSERT INTO countries (code, name_i18n) VALUES
  ('CA', '{"fr":"Canada","en":"Canada"}'),
  ('FR', '{"fr":"France","en":"France"}'),
  ('SN', '{"fr":"Sénégal","en":"Senegal"}')
ON CONFLICT (code) DO NOTHING;

-- Juridictions (référentiels pédagogiques sélectionnables, §9)
INSERT INTO jurisdictions (code, country_code, kind, name_i18n, default_locale) VALUES
  ('CA',            'CA', 'country',   '{"fr":"Canada (autres provinces)","en":"Canada (other provinces)"}', 'en'),
  ('FR',            'FR', 'country',   '{"fr":"France","en":"France"}', 'fr'),
  ('SN',            'SN', 'country',   '{"fr":"Sénégal","en":"Senegal"}', 'fr'),
  ('FRANCOPHONIE',  NULL, 'framework', '{"fr":"Autres pays francophones","en":"Other French-speaking countries"}', 'fr'),
  ('INTL',          NULL, 'framework', '{"fr":"Référentiel international général","en":"General international framework"}', 'en')
ON CONFLICT (code) DO NOTHING;

INSERT INTO jurisdictions (code, country_code, parent_id, kind, name_i18n, default_locale)
SELECT v.code, 'CA', p.id, 'province', v.name::jsonb, v.loc
FROM (VALUES
  ('CA-QC', '{"fr":"Québec","en":"Quebec"}', 'fr'),
  ('CA-ON', '{"fr":"Ontario","en":"Ontario"}', 'en'),
  ('CA-BC', '{"fr":"Colombie-Britannique","en":"British Columbia"}', 'en'),
  ('CA-AB', '{"fr":"Alberta","en":"Alberta"}', 'en')
) AS v(code, name, loc)
JOIN jurisdictions p ON p.code = 'CA'
ON CONFLICT (code) DO NOTHING;

-- Rôles (§8) + relecteur médical et administrateur d'établissement (ajouts proposés)
INSERT INTO roles (id, code, name_i18n) VALUES
  (1, 'student',          '{"fr":"Étudiant","en":"Student"}'),
  (2, 'instructor',       '{"fr":"Formateur","en":"Instructor"}'),
  (3, 'admin',            '{"fr":"Administrateur","en":"Administrator"}'),
  (4, 'medical_reviewer', '{"fr":"Relecteur médical","en":"Medical reviewer"}'),
  (5, 'org_admin',        '{"fr":"Administrateur d''établissement","en":"Organization administrator"}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO permissions (code, description) VALUES
  ('catalog:read',          'Consulter le catalogue publié'),
  ('learning:participate',  'Suivre des leçons, quiz, examens et simulations'),
  ('tutor:use',             'Utiliser le tuteur IA (selon quota du plan)'),
  ('content:create',        'Créer et modifier des brouillons de contenu'),
  ('content:review',        'Relire et approuver un contenu médical'),
  ('content:publish',       'Publier un contenu approuvé'),
  ('exam:create',           'Créer des examens et banques de questions'),
  ('exam:grade',            'Corriger des tentatives (réponses courtes, ECOS)'),
  ('competency:verify',     'Attester une compétence vérifiée en pratique'),
  ('cohort:manage',         'Gérer des groupes et inscriptions'),
  ('student:view_progress', 'Consulter la progression des étudiants suivis'),
  ('org:manage',            'Gérer un établissement'),
  ('user:manage',           'Gérer les comptes utilisateurs'),
  ('billing:manage',        'Superviser abonnements et facturation'),
  ('audit:read',            'Consulter le journal d''audit'),
  ('reference:manage',      'Gérer juridictions, métiers, spécialités, sources IA')
ON CONFLICT (code) DO NOTHING;

INSERT INTO role_permissions (role_id, permission_code)
SELECT r.id, p.code FROM roles r, permissions p
WHERE (r.code = 'student'          AND p.code IN ('catalog:read','learning:participate','tutor:use'))
   OR (r.code = 'instructor'       AND p.code IN ('catalog:read','learning:participate','tutor:use','content:create',
                                                  'exam:create','exam:grade','competency:verify','cohort:manage','student:view_progress'))
   OR (r.code = 'medical_reviewer' AND p.code IN ('catalog:read','content:review','content:publish'))
   OR (r.code = 'org_admin'        AND p.code IN ('catalog:read','cohort:manage','student:view_progress','org:manage'))
   OR (r.code = 'admin')
ON CONFLICT DO NOTHING;

-- Métiers par juridiction : jamais d'équivalence implicite entre titres (§9).
INSERT INTO professions (jurisdiction_id, code, title_i18n, category, is_regulated, regulator_name, regulator_url)
SELECT j.id, v.code, v.title::jsonb, v.category, v.reg, v.regulator, v.url
FROM (VALUES
  ('CA-QC','pab','{"fr":"Préposé(e) aux bénéficiaires","en":"Patient attendant (PAB)"}','care_assistant',false,NULL,NULL),
  ('CA-QC','infirmiere_auxiliaire','{"fr":"Infirmier(ère) auxiliaire","en":"Licensed practical nurse (Quebec)"}','practical_nurse',true,'Ordre des infirmières et infirmiers auxiliaires du Québec (OIIAQ)','https://www.oiiaq.org'),
  ('CA-QC','infirmiere','{"fr":"Infirmier(ère)","en":"Nurse (Quebec)"}','registered_nurse',true,'Ordre des infirmières et infirmiers du Québec (OIIQ)','https://www.oiiq.org'),
  ('CA-ON','psw','{"fr":"Préposé(e) aux services de soutien personnel","en":"Personal support worker (PSW)"}','care_assistant',false,NULL,NULL),
  ('CA-ON','rpn','{"fr":"Infirmier(ère) auxiliaire autorisé(e)","en":"Registered practical nurse (RPN)"}','practical_nurse',true,'College of Nurses of Ontario (CNO)','https://www.cno.org'),
  ('CA-ON','rn','{"fr":"Infirmier(ère) autorisé(e)","en":"Registered nurse (RN)"}','registered_nurse',true,'College of Nurses of Ontario (CNO)','https://www.cno.org'),
  ('FR','aide_soignant','{"fr":"Aide-soignant(e)","en":"Healthcare assistant (France)"}','care_assistant',true,'Diplôme d''État d''aide-soignant (Ministère chargé de la santé)',NULL),
  ('FR','infirmier','{"fr":"Infirmier(ère) diplômé(e) d''État","en":"State-registered nurse (France)"}','registered_nurse',true,'Ordre national des infirmiers','https://www.ordre-infirmiers.fr'),
  ('SN','aide_infirmier','{"fr":"Aide-infirmier(ère)","en":"Nursing aide (Senegal)"}','care_assistant',false,NULL,NULL),
  ('SN','infirmier_etat','{"fr":"Infirmier(ère) d''État","en":"State nurse (Senegal)"}','registered_nurse',true,NULL,NULL),
  ('INTL','care_assistant_generic','{"fr":"Assistant(e) en soins (cadre général)","en":"Care assistant (general framework)"}','care_assistant',false,NULL,NULL),
  ('INTL','nurse_generic','{"fr":"Infirmier(ère) (cadre général)","en":"Nurse (general framework)"}','registered_nurse',false,NULL,NULL)
) AS v(jcode, code, title, category, reg, regulator, url)
JOIN jurisdictions j ON j.code = v.jcode
ON CONFLICT (jurisdiction_id, code) DO NOTHING;

-- Spécialités (§1 A–H)
INSERT INTO specialties (code, catalog_group, name_i18n, sort_order) VALUES
  ('care_assistant_core',      'A', '{"fr":"Assistant infirmier / Aide-soignant","en":"Nursing assistant / Care aide"}', 10),
  ('general_nursing',          'B', '{"fr":"Soins infirmiers généraux","en":"General nursing"}', 20),
  ('geriatrics',               'C', '{"fr":"Gériatrie","en":"Geriatrics"}', 30),
  ('pediatrics',               'D', '{"fr":"Pédiatrie","en":"Pediatrics"}', 40),
  ('maternity_neonatal',       'E', '{"fr":"Maternité et néonatologie","en":"Maternity and neonatal care"}', 50),
  ('emergency_first_aid',      'F', '{"fr":"Urgences et premiers secours","en":"Emergency and first aid"}', 60),
  ('mental_health',            'G', '{"fr":"Santé mentale","en":"Mental health"}', 70),
  ('cardiology',               'H', '{"fr":"Cardiologie","en":"Cardiology"}', 100),
  ('neurology',                'H', '{"fr":"Neurologie","en":"Neurology"}', 101),
  ('pulmonology',              'H', '{"fr":"Pneumologie","en":"Pulmonology"}', 102),
  ('gastroenterology',         'H', '{"fr":"Gastroentérologie","en":"Gastroenterology"}', 103),
  ('nephrology',               'H', '{"fr":"Néphrologie","en":"Nephrology"}', 104),
  ('endocrinology',            'H', '{"fr":"Endocrinologie","en":"Endocrinology"}', 105),
  ('diabetology',              'H', '{"fr":"Diabétologie","en":"Diabetes care"}', 106),
  ('oncology',                 'H', '{"fr":"Oncologie","en":"Oncology"}', 107),
  ('surgery',                  'H', '{"fr":"Chirurgie","en":"Surgery"}', 108),
  ('orthopedics',              'H', '{"fr":"Orthopédie","en":"Orthopedics"}', 109),
  ('dermatology',              'H', '{"fr":"Dermatologie","en":"Dermatology"}', 110),
  ('infectious_diseases',      'H', '{"fr":"Maladies infectieuses","en":"Infectious diseases"}', 111),
  ('intensive_care',           'H', '{"fr":"Soins intensifs","en":"Intensive care"}', 112),
  ('palliative_care',          'H', '{"fr":"Soins palliatifs","en":"Palliative care"}', 113),
  ('rehabilitation',           'H', '{"fr":"Réadaptation","en":"Rehabilitation"}', 114),
  ('community_health',         'H', '{"fr":"Santé communautaire","en":"Community health"}', 115),
  ('public_health',            'H', '{"fr":"Santé publique","en":"Public health"}', 116),
  ('home_care',                'H', '{"fr":"Soins à domicile","en":"Home care"}', 117),
  ('clinical_nutrition',       'H', '{"fr":"Nutrition clinique","en":"Clinical nutrition"}', 118),
  ('infection_prevention',     'H', '{"fr":"Prévention et contrôle des infections","en":"Infection prevention and control"}', 119)
ON CONFLICT (code) DO NOTHING;

-- Plans (§12). Aucun stripe_price_id : paiement réel désactivé par défaut.
INSERT INTO plans (code, tier, name_i18n, billing_interval, price_cents, currency, ai_daily_message_quota, features) VALUES
  ('free',            'free',         '{"fr":"Gratuit","en":"Free"}',               NULL,    0,    'CAD', 10,
     '{"catalog":"intro","exams":"basic","offline":false,"advanced_stats":false}'),
  ('premium_monthly', 'premium',      '{"fr":"Premium mensuel","en":"Premium monthly"}', 'month', 1999, 'CAD', 100,
     '{"catalog":"full","exams":"advanced","offline":true,"advanced_stats":true}'),
  ('organization',    'organization', '{"fr":"Établissement","en":"Organization"}',  'year',  0,    'CAD', 150,
     '{"catalog":"full","exams":"advanced","cohorts":true,"reports":true,"custom_courses":true}')
ON CONFLICT (code) DO NOTHING;

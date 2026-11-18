-- =============================================================================
-- Structure du parcours pilote (lot 1.2) — PAB Québec,
-- module « Hygiène des mains et prévention des infections ».
--
-- STRUCTURE UNIQUEMENT, TOUT EN BROUILLON (status = 'draft') :
--   * aucun contenu médical n'est rédigé ici ;
--   * rien n'est visible dans le catalogue public (l'API n'expose que 'published') ;
--   * la publication exigera la relecture d'un professionnel qualifié habilité
--     (contraintes 0001 et déclencheurs 0003).
-- Idempotent.
-- =============================================================================

INSERT INTO programs (code, profession_id, specialty_id, title_i18n, summary_i18n, level, access_tier, status)
SELECT 'pab-qc-socle', p.id, s.id,
       '{"fr":"Préposé(e) aux bénéficiaires — Socle","en":"Patient attendant (Quebec) — Foundations"}',
       '{"fr":"Parcours pilote en préparation. Contenu en cours de rédaction et de validation.","en":"Pilot path in preparation. Content is being written and reviewed."}',
       'beginner', 'free', 'draft'
FROM professions p
JOIN jurisdictions j ON j.id = p.jurisdiction_id AND j.code = 'CA-QC'
JOIN specialties s ON s.code = 'care_assistant_core'
WHERE p.code = 'pab'
ON CONFLICT (code) DO NOTHING;

INSERT INTO modules (code, specialty_id, level, title_i18n, summary_i18n, estimated_minutes, access_tier, status)
SELECT 'hygiene-mains-prevention-infections', s.id, 'beginner',
       '{"fr":"Hygiène des mains et prévention des infections","en":"Hand hygiene and infection prevention"}',
       '{"fr":"Module pilote — en préparation.","en":"Pilot module — in preparation."}',
       60, 'free', 'draft'
FROM specialties s WHERE s.code = 'care_assistant_core'
ON CONFLICT (code) DO NOTHING;

INSERT INTO program_modules (program_id, module_id, position, is_required)
SELECT p.id, m.id, 0, true
FROM programs p, modules m
WHERE p.code = 'pab-qc-socle' AND m.code = 'hygiene-mains-prevention-infections'
  -- ON CONFLICT impossible ici (contrainte d'unicité différable) : garde explicite.
  AND NOT EXISTS (SELECT 1 FROM program_modules pm WHERE pm.program_id = p.id AND pm.module_id = m.id);

-- Quatre leçons (squelette). Les titres sont des intitulés de travail, sans affirmation médicale.
INSERT INTO lessons (module_id, code, position, kind, estimated_minutes)
SELECT m.id, v.code, v.pos, v.kind, v.minutes
FROM modules m,
     (VALUES ('l01-introduction', 0, 'theory', 10),
             ('l02-principes',    1, 'theory', 20),
             ('l03-mise-en-pratique', 2, 'interactive', 20),
             ('l04-revision',     3, 'revision_sheet', 10)) AS v(code, pos, kind, minutes)
WHERE m.code = 'hygiene-mains-prevention-infections'
  AND NOT EXISTS (SELECT 1 FROM lessons x WHERE x.module_id = m.id AND x.code = v.code);

INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status, change_summary)
SELECT l.id, v.locale, 1, v.title,
       '{"blocks":[],"editorial_status":"to_write","review_required":"qualified_professional"}'::jsonb,
       'draft', 'Squelette créé par le lot 1.2 — aucun contenu médical'
FROM lessons l
JOIN modules m ON m.id = l.module_id AND m.code = 'hygiene-mains-prevention-infections'
JOIN (VALUES
        ('l01-introduction',     'fr', 'Leçon 1 — Introduction (à rédiger)'),
        ('l01-introduction',     'en', 'Lesson 1 — Introduction (to be written)'),
        ('l02-principes',        'fr', 'Leçon 2 — Principes (à rédiger)'),
        ('l02-principes',        'en', 'Lesson 2 — Principles (to be written)'),
        ('l03-mise-en-pratique', 'fr', 'Leçon 3 — Mise en pratique (à rédiger)'),
        ('l03-mise-en-pratique', 'en', 'Lesson 3 — Practice (to be written)'),
        ('l04-revision',         'fr', 'Leçon 4 — Fiche de révision (à rédiger)'),
        ('l04-revision',         'en', 'Lesson 4 — Revision sheet (to be written)')
     ) AS v(code, locale, title) ON v.code = l.code
ON CONFLICT DO NOTHING;

"""Parcours pilote PAB Québec (lot 1.2) : structure seule, entièrement en brouillon."""
from __future__ import annotations

from conftest import SEEDS


def test_pilot_structure_exists_and_is_linked_to_pab_quebec(db):
    row = db.sql("""SELECT p.status, pr.code, j.code, m.status, count(l.id)
                    FROM programs p JOIN professions pr ON pr.id = p.profession_id
                    JOIN jurisdictions j ON j.id = pr.jurisdiction_id
                    JOIN program_modules pm ON pm.program_id = p.id JOIN modules m ON m.id = pm.module_id
                    JOIN lessons l ON l.module_id = m.id
                    WHERE p.code = 'pab-qc-socle' AND m.code = 'hygiene-mains-prevention-infections'
                    GROUP BY 1, 2, 3, 4""")
    assert row == "draft|pab|CA-QC|draft|4"


def test_pilot_contains_no_published_or_reviewed_content(db):
    assert db.sql("""SELECT count(*) FROM lesson_versions lv JOIN lessons l ON l.id = lv.lesson_id
                     JOIN modules m ON m.id = l.module_id
                     WHERE m.code = 'hygiene-mains-prevention-infections'
                       AND (lv.status <> 'draft' OR lv.reviewed_by IS NOT NULL)""") == "0"
    # Chaque leçon existe en FR et en EN, sans aucun bloc de contenu rédigé.
    assert db.sql("""SELECT count(*) FROM lesson_versions lv JOIN lessons l ON l.id = lv.lesson_id
                     JOIN modules m ON m.id = l.module_id
                     WHERE m.code = 'hygiene-mains-prevention-infections'
                       AND jsonb_array_length(lv.body->'blocks') = 0""") == "8"


def test_pilot_seed_is_idempotent(db):
    before = db.sql("SELECT (SELECT count(*) FROM lessons)::text || '/' || (SELECT count(*) FROM lesson_versions)")
    db.file(SEEDS / "0002_pilot_pab_structure.sql")
    after = db.sql("SELECT (SELECT count(*) FROM lessons)::text || '/' || (SELECT count(*) FROM lesson_versions)")
    assert before == after

"""Résolution de l'édition publiée d'un programme (étape 2.2.2-c, D12 / ADR-0010).

Le catalogue public ne lit PLUS la structure de travail (`program_modules`) ni « la meilleure version
publiée » d'une leçon : il lit exclusivement l'édition `published` du programme (au plus une, index
0005) à travers `nl_release_lessons(release)` (0006), commune aux deux structures :
`manifest_v1` (éditions historiques 0005) et `module_versions` (éditions relationnelles).

Un programme n'est EXPOSÉ que si son édition publiée est complète et cohérente :

* programme `published`, hors établissement, avec une édition `published` non vide ;
* chaque module cité : public (hors établissement) et `published` ; chaque version de module citée
  (structure relationnelle) : `published`, du bon module, non vide ;
* chaque leçon citée : appartient au module, possède une version FRANÇAISE (langue de base) ;
* chaque version de leçon citée : `published`, de la bonne leçon et de la langue annoncée, commune
  ou propre à la juridiction du métier du programme ;
* programme médical : chaque version de leçon cite une source active et vérifiée (0006).

Sinon le programme n'est ni listé ni détaillé (404), comme un programme en préparation : aucun
parcours incomplet, aucun choix implicite de version. Aucune écriture, aucune migration.
"""

from __future__ import annotations

from sqlalchemy import Integer, column, text
from sqlalchemy.dialects.postgresql import UUID

EXPOSABLE_SQL = """
SELECT r.program_id, r.id AS release_id, r.release_no,
       (SELECT count(DISTINCT x.module_id) FROM nl_release_lessons(r.id) x) AS module_count
FROM program_releases r
JOIN programs p ON p.id = r.program_id
JOIN professions pf ON pf.id = p.profession_id
WHERE r.status = 'published' AND p.status = 'published' AND p.organization_id IS NULL
  AND EXISTS (SELECT 1 FROM nl_release_lessons(r.id))
  -- Structure relationnelle : aucune version de module vide (elle disparaîtrait silencieusement).
  AND NOT EXISTS (
    SELECT 1 FROM program_release_modules rm
    WHERE rm.release_id = r.id
      AND NOT EXISTS (SELECT 1 FROM module_version_lessons ml WHERE ml.module_version_id = rm.module_version_id))
  -- Chaque leçon citée possède une version française (langue de base).
  AND NOT EXISTS (
    SELECT 1 FROM nl_release_lessons(r.id) y
    WHERE NOT EXISTS (SELECT 1 FROM nl_release_lessons(r.id) z
                      WHERE z.lesson_id = y.lesson_id AND z.module_id = y.module_id AND z.locale = 'fr'))
  -- Chaque élément cité est public, publié et cohérent.
  AND NOT EXISTS (
    SELECT 1
    FROM nl_release_lessons(r.id) x
    LEFT JOIN modules m ON m.id = x.module_id
    LEFT JOIN lessons l ON l.id = x.lesson_id
    LEFT JOIN lesson_versions lv ON lv.id = x.lesson_version_id
    LEFT JOIN module_versions mv ON mv.id = x.module_version_id
    WHERE m.id IS NULL OR m.organization_id IS NOT NULL OR m.status <> 'published'
       OR l.id IS NULL OR l.module_id <> x.module_id
       OR lv.id IS NULL OR lv.status <> 'published' OR lv.lesson_id <> x.lesson_id OR lv.locale <> x.locale
       OR (lv.jurisdiction_id IS NOT NULL AND lv.jurisdiction_id IS DISTINCT FROM pf.jurisdiction_id)
       OR (x.module_version_id IS NOT NULL
           AND (mv.id IS NULL OR mv.status <> 'published' OR mv.module_id <> x.module_id))
       OR (p.is_medical AND NOT nl_has_verified_source(lv.id)))
"""


def exposable_releases():  # noqa: ANN201 - sous-requête SQLAlchemy
    """Sous-requête (program_id, release_id, release_no, module_count) des éditions exposables."""
    return (
        text(EXPOSABLE_SQL)
        .columns(
            column("program_id", UUID(as_uuid=True)),
            column("release_id", UUID(as_uuid=True)),
            column("release_no", Integer),
            column("module_count", Integer),
        )
        .subquery("exposable")
    )


RELEASE_CONTENT = text(
    "SELECT module_id, module_version_id, module_position, module_required, lesson_id, lesson_position, "
    "locale, lesson_version_id FROM nl_release_lessons(:release) "
    "ORDER BY module_position, lesson_position, locale"
)

RELEASE_PREREQUISITES = text(
    "SELECT module_id, prerequisite_module_id FROM program_release_prerequisites WHERE release_id = :release "
    "ORDER BY module_id, prerequisite_module_id"
)

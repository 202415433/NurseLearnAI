"""Contrôles de publication côté API (défense en profondeur, avant les déclencheurs de 0006).

La base revalide déjà états, empreintes, indépendance et sources ; l'API vérifie en plus la chaîne
des décisions éditoriales (une approbation journalisée par le relecteur enregistré, sur l'empreinte
courante) et la validité des échéances de révision, et rend un diagnostic lisible sans contenu.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError
from app.domains.editorial.errors import BASE, problem

_CHECKS = text(
    """
WITH rel AS (
  SELECT r.id, r.reviewed_by, r.content_sha256, p.is_medical
  FROM program_releases r JOIN programs p ON p.id = r.program_id WHERE r.id = :r
), mvs AS (
  SELECT mv.* FROM program_release_modules rm JOIN module_versions mv ON mv.id = rm.module_version_id
  WHERE rm.release_id = :r
), lvs AS (
  SELECT DISTINCT lv.* FROM nl_release_lessons(:r) x JOIN lesson_versions lv ON lv.id = x.lesson_version_id
)
SELECT 'release_decision' AS issue, rel.id AS entity_id FROM rel
 WHERE NOT EXISTS (SELECT 1 FROM editorial_decisions d WHERE d.entity_type = 'program_release'
                   AND d.entity_id = rel.id AND d.to_status = 'approved' AND d.actor_id = rel.reviewed_by
                   AND d.content_sha256 = rel.content_sha256)
UNION ALL
SELECT 'module_version_state', mvs.id FROM mvs WHERE mvs.status NOT IN ('approved','published')
UNION ALL
SELECT 'module_version_decision', mvs.id FROM mvs
 WHERE NOT EXISTS (SELECT 1 FROM editorial_decisions d WHERE d.entity_type = 'module_version'
                   AND d.entity_id = mvs.id AND d.to_status = 'approved' AND d.actor_id = mvs.reviewed_by)
UNION ALL
SELECT 'lesson_version_state', lvs.id FROM lvs WHERE lvs.status NOT IN ('approved','published')
UNION ALL
SELECT 'lesson_version_decision', lvs.id FROM lvs
 WHERE NOT EXISTS (SELECT 1 FROM editorial_decisions d WHERE d.entity_type = 'lesson_version'
                   AND d.entity_id = lvs.id AND d.to_status = 'approved' AND d.actor_id = lvs.reviewed_by)
UNION ALL
SELECT 'review_expired', lvs.id FROM lvs WHERE lvs.next_review_due IS NULL OR lvs.next_review_due <= current_date
UNION ALL
SELECT 'missing_verified_source', lvs.id FROM lvs, rel
 WHERE rel.is_medical AND NOT nl_has_verified_source(lvs.id)
UNION ALL
SELECT 'withdrawn_source', lvs.id FROM lvs
 WHERE EXISTS (SELECT 1 FROM lesson_version_references lr JOIN medical_references mr ON mr.id = lr.reference_id
               WHERE lr.lesson_version_id = lvs.id AND mr.status <> 'active')
ORDER BY 1, 2
"""
)


async def publication_issues(db: AsyncSession, release_id: Any) -> list[dict[str, Any]]:
    rows = await db.execute(_CHECKS, {"r": release_id})
    return [{"issue": r.issue, "entity_id": str(r.entity_id)} for r in rows]


async def ensure_publishable(db: AsyncSession, release_id: Any) -> None:
    issues = await publication_issues(db, release_id)
    if issues:
        raise ProblemError(
            422,
            "Publication refusée : conditions éditoriales ou médicales non remplies",
            type_=BASE + "publication-requirements",
            extra={"code": "publication_requirements", "issues": issues[:50]},
        )


# Suppression d'un brouillon : uniquement s'il n'a laissé AUCUNE trace. Chaque table qui référence
# une version est vérifiée explicitement, y compris celles dont la clé étrangère est en CASCADE
# (signalements de contenu) : une suppression n'emporte jamais une autre donnée.
_DELETE_BLOCKERS = {
    "lesson_version": text(
        """
SELECT 'history' FROM editorial_decisions WHERE entity_type = 'lesson_version' AND entity_id = :id
UNION ALL SELECT 'in_use' FROM module_version_lesson_versions WHERE lesson_version_id = :id
UNION ALL SELECT 'history' FROM content_issue_reports WHERE lesson_version_id = :id
UNION ALL SELECT 'history' FROM learning_events WHERE lesson_version_id = :id
UNION ALL SELECT 'history' FROM student_progress WHERE lesson_version_id = :id
LIMIT 1
"""
    ),
    "module_version": text(
        """
SELECT 'history' FROM editorial_decisions WHERE entity_type = 'module_version' AND entity_id = :id
UNION ALL SELECT 'in_use' FROM program_release_modules WHERE module_version_id = :id
UNION ALL SELECT 'history' FROM learning_events WHERE module_version_id = :id
UNION ALL SELECT 'history' FROM student_progress WHERE module_version_id = :id
LIMIT 1
"""
    ),
}


async def ensure_deletable(db: AsyncSession, entity_type: str, entity_id: Any) -> None:
    blocker = (await db.execute(_DELETE_BLOCKERS[entity_type], {"id": entity_id})).scalar()
    if blocker is not None:
        raise problem(str(blocker))

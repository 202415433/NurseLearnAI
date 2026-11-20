# Préparation de l'étape 2.2.2-b — API éditoriale

> État : **décision en attente**. L'étape 2.2.2-b ne démarre qu'avec votre autorisation, après validation Windows de 2.2.2-a (`validate-phase2.2.2a.ps1` sans échec) et son intégration.

## 1. Ce que 2.2.2-a fournit

| Besoin de l'API (plan 2.2.2, § 3) | Fourni par la base (0005 + 0006) |
|---|---|
| Versions de leçon : brouillon, copie, transitions | `lesson_versions` + workflow 0005 (acteur déclaré) |
| Versions de modules (D12) | `module_versions`, `module_version_lessons`, `module_version_lesson_versions` + workflow |
| Éditions relationnelles et prérequis | `program_releases (structure = module_versions)`, `program_release_modules`, `program_release_prerequisites` |
| Sources vérifiées | `medical_references.verified_by` posé par la base pour un relecteur habilité |
| Indépendance, immuabilité, isolation, journal | Déclencheurs et clés étrangères (voir VALIDATION-PHASE2.2.2A, § 2.4) |
| Codes d'erreur stables | `HINT nl:<code>` sur les règles de 0006 |
| Modèles SQLAlchemy | `ModuleVersion`, `ModuleVersionLesson`, `ModuleVersionLessonVersion`, `ProgramReleaseModule`, `ProgramReleasePrerequisite`, colonnes `structure` et `module_version_id` |

## 2. Points d'attention pour 2.2.2-b

1. Toujours écrire dans une transaction qui déclare `nl.actor_id` = utilisateur de la session (jamais une valeur du client) ; `nl.decision_comment` pour les demandes de modifications.
2. Traduire : `HINT nl:*` → `problem+json` stable ; erreurs de 0005 sans `HINT` → SQLSTATE + correspondance de message (liste fermée), sans exposer de SQL.
3. Concurrence : `If-Match` sur les brouillons (empreinte calculée par l'API).
4. Lecture d'une édition : uniquement via `nl_release_lessons()` (deux structures).
5. Ne jamais exposer de route qui publie une version de leçon ou de module isolément : la publication passe par l'édition.

## 3. Décisions restantes

| # | Sujet | État | Bloquant pour 2.2.2-b ? |
|---|---|---|---|
| D2 | `medical_review` | ✅ Confirmé | — |
| D12 | Versions de modules par édition | ✅ Décidé et mis en œuvre (0006) | — |
| D11 | ADR-0009 / ADR-0010 | ADR-0009 « proposé » ; ADR-0010 décision approuvée, mise en œuvre à valider avec 2.2.2-a | Non |
| D14 | Rôles PostgreSQL séparés | Lot 2.5 | Non (requis avant production) |
| D1, D4–D10 | Voir le plan 2.2 | Non confirmées | Non pour 2.2.2-b ; D7 bloque 2.2.3, D9 et D10 bloquent toute publication réelle |

## 4. Prérequis

1. PR #9 (documentation D12 et plan 2.2.2) fusionnée.
2. 2.2.2-a validée sur Windows, poussée et fusionnée sur votre autorisation.

## 5. Recommandation

**GO conditionnel** pour 2.2.2-b après ces prérequis. Aucun développement de 2.2.2-b ni de 2.2.2-c n'a été commencé.

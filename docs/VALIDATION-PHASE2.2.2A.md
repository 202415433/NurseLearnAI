# Validation de l'étape 2.2.2-a — Migration 0006 (versions de modules indépendantes)

Branche : `phase-2.2.2a-versioning-migration` (locale, **non poussée**). Base : branche documentaire `docs-phase-2.2.2-plan` (PR #9), à rebaser sur `main` après sa fusion.
Références : [ADR-0010](adr/0010-versions-de-modules-par-edition.md) (D12) · [ADR-0009](adr/0009-editions-figees-et-workflow-editorial.md) · [plan 2.2.2](PHASE2.2.2-PLAN.md) · [validation 2.2.1](VALIDATION-PHASE2.2.1.md) · [préparation 2.2.2-b](PHASE2.2.2B-READINESS.md)

> Aucune ligne de l'API éditoriale (2.2.2-b) ni du catalogue (2.2.2-c). Aucun contenu médical publié, aucun relecteur fictif hors des bases de test.

## 1. Analyse préalable (dépendances et invariants)

| Élément existant | Dépendance avec D12 | Traitement dans 0006 |
|---|---|---|
| `ux_lesson_versions_one_published` (0001) | Une seule version publiée par leçon × langue × juridiction : empêche deux programmes de diffuser deux versions | **Supprimé** ; remplacé par un index non unique `ix_lesson_versions_published` |
| `nl_program_release_after` (0005) | Archivage implicite des versions remplacées, blocage croisé entre programmes | **Redéfini** : aucune modification d'un autre programme |
| `nl_validate_release`, manifeste JSON (0005) | Liens vérifiés par déclencheur seulement | Conservé pour l'historique (`manifest_v1`), sources désormais vérifiées ; nouvelles éditions relationnelles |
| Lectures du manifeste dans `nl_module_catalog_guard`, `nl_enrollment_guard`, `nl_learning_event_check`, `nl_student_progress_check`, `nl_lesson_version_delete` | JSON uniquement | **Redéfinies** pour lire `nl_release_lessons()` (deux structures) |
| `module_prerequisites` (0001), globaux | Non figés par édition | Prérequis figés par édition (`program_release_prerequisites`) ; table globale inchangée |
| Source « active » (0005) | Une source créée sans vérification comptait | Source **active et vérifiée** (`verified_by` posé par un relecteur habilité) |
| `editorial_decisions.entity_type` (0005) | Pas d'entité « version de module » | Contrainte remplacée (`ck_editorial_decisions_entity_type`) |
| Questions et simulations (`in_review`, 0001) | Hors périmètre (lot 2.3) | **Non modifiées** (D2) |

## 2. Migration `0006_independent_program_versions`

Fichiers : `database/schema/0006_independent_program_versions.sql`, `…_down.sql`, révision Alembic **`0006_independent_versions`** (après `0005_learning_engine` ; identifiant raccourci car `alembic_version.version_num` est limité à 32 caractères — garde-fou `test_revision_ids_fit_alembic_version_table`). **Aucune migration historique modifiée.** 71 tables (66 + 5).

### 2.1 Tables ajoutées

| Table | Rôle | Intégrité |
|---|---|---|
| `module_versions` | Version immuable d'un module | Workflow à cinq états ; `UNIQUE (module_id, version_no)` continu ; `UNIQUE (id, module_id)` ; relecteur ≠ auteur ; champs requis selon l'état ; suppression d'un brouillon seulement |
| `module_version_lessons` | Leçons d'une version de module (ordre, obligatoire) | FK composées `(module_version_id, module_id) → module_versions` et `(lesson_id, module_id) → lessons` : une leçon d'un autre module est **impossible par construction** ; position unique |
| `module_version_lesson_versions` | Version exacte d'une leçon par langue | FK composée `(lesson_version_id, lesson_id, locale) → lesson_versions` : version d'une autre leçon ou d'une autre langue **impossible** ; FK vers la leçon de la version de module ; `ON DELETE RESTRICT` |
| `program_release_modules` | Versions de modules citées par une édition | `PRIMARY KEY (release_id, module_id)` (une version par module) ; FK composée `(module_version_id, module_id) → module_versions` ; position unique |
| `program_release_prerequisites` | Prérequis figés par édition | FK composées vers les modules **de la même édition** ; pas d'auto-prérequis ; absence de cycle vérifiée à la soumission |

### 2.2 Colonnes et contraintes ajoutées

`program_releases.structure` (`manifest_v1` pour les éditions existantes — remplissage sans réécriture —, `module_versions` par défaut et imposé pour toute nouvelle édition) ; `learning_events.module_version_id`, `student_progress.module_version_id` ; `medical_references.verified_by` ; contraintes support `lessons (id, module_id)` et `lesson_versions (id, lesson_id, locale)`.

### 2.3 Fonctions et déclencheurs

| Nouveaux | Rôle |
|---|---|
| `nl_release_lessons(release)` | Contenu d'une édition (modules, versions de modules, leçons, langues, versions de leçons) pour les deux structures |
| `nl_validate_module_version`, `nl_module_version_insert/update/log/delete`, `nl_module_version_items_guard` | Workflow, immuabilité et journal des versions de modules |
| `nl_validate_release_v2`, `nl_release_v2_sha256`, `nl_module_version_sha256` | Validation et empreintes des éditions relationnelles |
| `nl_program_release_structure_guard`, `nl_release_items_guard` | Structure imposée, contenu figé hors brouillon, isolation des établissements |
| `nl_reference_verification`, `nl_has_verified_source` | Vérification explicite des sources par un relecteur habilité (acteur déclaré) |
| `nl_lesson_version_in_use_guard`, `nl_*_in_published_release` | Archivage refusé tant qu'une édition publiée cite la version |

Redéfinis (corps d'origine restaurés au retour arrière) : `nl_validate_release`, `nl_program_release_update`, `nl_program_release_after`, `nl_module_catalog_guard`, `nl_enrollment_guard`, `nl_learning_event_check`, `nl_student_progress_check`, `nl_lesson_version_delete`. Les erreurs levées par les fonctions de 0006 portent un code stable `HINT = 'nl:<code>'` (A10).

### 2.4 Invariants garantis par PostgreSQL

1. Deux programmes peuvent suivre deux versions différentes d'un même module ; plusieurs versions publiées d'une leçon coexistent.
2. Publier une édition ne modifie ni n'archive rien d'un autre programme.
3. Une édition cite des versions immuables (versions de modules et de leçons figées dès la soumission ; empreintes revérifiées).
4. Les inscriptions restent sur leur édition initiale ; seul un changement explicite vers une édition publiée plus récente du même programme est accepté ; aucune bascule implicite.
5. Une version citée par une édition publiée ne peut être ni archivée ni supprimée.
6. Prérequis figés par édition, entre modules de l'édition, sans cycle.
7. Isolation : module privé seulement dans une édition de son établissement ; habilitations vérifiées dans la portée du module.
8. Workflow à cinq états, auteur ≠ relecteur, relecteur habilité et indépendant des auteurs des versions incluses, décisions immuables, source **active et vérifiée** pour tout programme médical.

## 3. Résultats dans l'environnement de développement (PostgreSQL 16 réel)

| Contrôle | Résultat |
|---|---|
| 0006 montée / retour / remontée (base vide, signature de schéma **incluant le code des fonctions** identique) | ✅ |
| Chaîne Alembic 0001 → 0006 montée / descente / remontée (`tests/test_migrations.py`) | ✅ (révision pgvector : CI et Docker) |
| Nouveaux tests 0006 (`database/tests/test_program_versions.py`) | ✅ 21/21 |
| Non-régression 0005 historique (`test_learning_engine.py`, base arrêtée à 0005) | ✅ 45/45 |
| Suite complète `database/tests` | ✅ 121/121 |
| Fixture CI de démonstration (version de module explicite), rejouée deux fois | ✅ |
| Parcours de la fixture des tests API rejoué en SQL (deux programmes, deux versions de module) | ✅ |
| `verify_head.sql` (71 tables, contrôles 0006 ; pgvector vérifié sous Docker) | ✅ |
| Ruff (lint + format), mypy | ✅ |
| Tests API, Vitest, Playwright, pile Docker | ⏳ sur Windows (`validate-phase2.2.2a.ps1`) — 1re exécution : échec au démarrage de l'API (identifiant de révision de 33 caractères), corrigé |

### Couverture des scénarios demandés

| Demande | Tests |
|---|---|
| Base vide | `test_empty_database_up_down_up_is_exact` |
| Base avec données de démonstration | `test_demo_fixture_runs_through_the_workflow_at_head` |
| Base avec éditions historiques 0005 et inscriptions existantes | `test_historical_0005_editions_and_enrollments_survive_and_coexist` (montée, événements toujours acceptés, retour arrière sûr puis refus dès qu'il existe des données 0006) |
| Plusieurs programmes partageant un module ; versions de leçons différentes | `test_two_programs_follow_different_versions_of_the_same_module` |
| Clés étrangères | `test_foreign_keys_forbid_incoherent_links` |
| Déclencheurs, immuabilité | `test_module_version_content_is_frozen_after_submission`, `test_release_content_is_frozen_after_submission`, `test_new_editions_must_cite_module_versions` |
| Workflow, auto-approbation, permissions, indépendance | `test_module_version_workflow_is_logged`, `test_module_version_review_rules`, `test_release_requires_approved_module_versions_and_independent_reviewer` |
| Prérequis | `test_prerequisites_are_frozen_and_acyclic` |
| Sources | `test_sources_must_be_active_and_explicitly_verified`, `test_non_medical_program_needs_no_source_but_still_review` |
| Isolation | `test_organization_isolation_of_module_versions` |
| Inscriptions, historique, idempotence | `test_two_programs_…`, `test_events_and_progress_track_the_module_version` |
| Archivage d'une version en usage | `test_versions_in_use_by_a_published_release_cannot_be_archived` |
| Audit des décisions | décisions `module_version` et `program_release` vérifiées dans les tests de workflow |

### Tests et fixtures adaptés

- `test_learning_engine.py` (0005) s'exécute désormais sur une base arrêtée à 0005 : il documente le comportement des éditions historiques (manifeste JSON), qui n'est plus celui des nouvelles éditions.
- `test_schema.py` : la règle du lot 1 « une seule version publiée par leçon × langue » est **levée volontairement** par D12 ; le test vérifie désormais la coexistence explicite.
- Fixtures API (`published_demo`) et CI (`e2e_demo_published.sql`) : versions de modules explicites, non médicales, menées par le workflow complet.

## 4. Retour arrière

- **Sûr** (aucune donnée propre à 0006) : restaure exactement le schéma 0005 (tables, contraintes, index, déclencheurs et **corps de fonctions** comparés par empreinte).
- **Refusé** avec un message listant les comptes dès qu'existent : versions de modules, éditions relationnelles, décisions sur des versions de modules, événements ou progression liés à une version de module, sources vérifiées, ou plusieurs versions publiées d'une même leçon × langue × juridiction. Le schéma et les données restent alors **inchangés** (vérifié par signature).
- Le script de validation ne déclenche jamais de retour arrière sur la base de développement.

## 5. Résultats sur Windows

> ⏳ À compléter après `powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2a.ps1`.

## 6. Décisions techniques

| # | Décision | Justification |
|---|---|---|
| T1 | Deux tables pour le contenu d'une version de module (leçons ; versions par langue) au lieu d'une | Position et caractère obligatoire saisis une fois par leçon ; clés étrangères composées sur chaque niveau |
| T2 | Éditions 0005 conservées en `manifest_v1`, sans conversion | Aucune réécriture de données ; nouvelles éditions relationnelles imposées |
| T3 | Vérification des sources par l'acteur déclaré, relecteur habilité | « Explicitement vérifiée » traçable (qui, quand) ; une date seule ne suffit plus |
| T4 | Codes `HINT nl:<code>` sur les erreurs de 0006 | Traduction fiable par l'API (2.2.2-b) |
| T5 | Publication d'une édition = publication des versions approuvées qu'elle cite, et de rien d'autre | Indépendance des programmes ; aucune action implicite ailleurs |

## 7. Limites

- Les erreurs levées par les fonctions **non redéfinies** de 0005 (workflow des versions de leçon, décisions) n'ont pas encore de `HINT` : l'API 2.2.2-b les traduira par SQLSTATE et message, ou une migration ultérieure les normalisera.
- Le catalogue public (lot 1.2) lit encore « les versions publiées » d'une leçon : plusieurs peuvent désormais coexister ; l'adaptation est l'étape 2.2.2-c.
- La base fait toujours confiance à l'acteur déclaré par l'application (D14, rôles PostgreSQL séparés au lot 2.5).
- Les prérequis globaux (`module_prerequisites`) restent une aide à la conception ; seuls les prérequis figés par édition font foi pour l'étudiant.

## 8. Risques

| Risque | Niveau | Atténuation |
|---|---|---|
| Catalogue affichant plusieurs versions publiées avant 2.2.2-c | Moyen | Aucune donnée concernée tant que deux éditions divergentes ne sont pas publiées ; 2.2.2-c dans le même sous-lot |
| Coût des fonctions qui parcourent les éditions publiées (archivage, événements) | Faible | Index sur les tables d'association ; volumes modestes ; à mesurer en 2.2.4 |
| Retour arrière impossible après usage réel de 0006 | Attendu | Refus explicite, sans perte ; sauvegarde avant toute migration en production |

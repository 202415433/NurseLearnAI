# Validation du sous-lot 2.2.1 — Modèle de données et règles éditoriales

Branche : `phase-2.2.1-learning-data-model` (locale, **non poussée**). Base : `main` @ `9671890` (Phase 2.1 fusionnée par #6, plan 2.2 par #7).
Références : [plan 2.2](PHASE2.2-PLAN.md) (branche documentaire) · [ADR-0009](adr/0009-editions-figees-et-workflow-editorial.md) (proposé) · [ADR-0004](adr/0004-relecture-quatre-yeux-et-attestations.md) · [préparation 2.2.2](PHASE2.2.2-READINESS.md)

> Aucun développement des sous-lots 2.2.2 et suivants. Aucun contenu médical publié ni déclaré validé.

## 1. Analyse de l'existant (62 tables après la Phase 2.1)

Réutilisées sans modification de structure : `programs`, `modules`, `program_modules`, `module_prerequisites`, `lessons`, `lesson_versions`, `lesson_version_references`, `medical_references`, `learning_resources`, `learning_objectives`, `objective_competencies`, `competencies`, `competency_scopes`, `cohorts`, `cohort_members`, `student_progress`, `audit_logs`, ainsi que les fonctions `nl_user_has_permission`, `nl_user_organizations`, `nl_forbid_modification`, `nl_set_updated_at` et l'index `ux_lesson_versions_one_published` (une version publiée par leçon × langue × juridiction).

## 2. Migration `0005_learning_engine` (additive, réversible)

Fichiers : `database/schema/0005_learning_engine.sql`, `0005_learning_engine_down.sql`, révision Alembic `0005_learning_engine` (après `0004_identity`). Aucune migration historique modifiée.

### 2.1 Nouvelles tables (66 au total)

| Table | Rôle | Intégrité |
|---|---|---|
| `program_releases` | Édition figée d'un programme (manifeste : modules, leçons, version exacte par langue) | `UNIQUE (program_id, release_no)` ; numérotation continue ; une seule édition publiée par programme (index partiel) ; relecteur ≠ auteur ; champs obligatoires selon l'état ; `UNIQUE (id, program_id)` pour les clés composées |
| `editorial_decisions` | Journal des décisions (soumis, modifications demandées, approuvé, publié, archivé, abandonné) | Ajout seul (UPDATE, DELETE, TRUNCATE refusés) ; insertion réservée aux déclencheurs ; motif obligatoire pour « modifications demandées » ; acteur, portée, empreinte |
| `enrollments` | Inscription d'un étudiant à un programme, rattachée à l'édition suivie | `UNIQUE (user_id, program_id)` ; FK composée (édition, programme) ; jamais supprimée ; édition seulement remplacée par une édition publiée plus récente ; isolation d'établissement et de cohorte ; point de reprise dans l'édition |
| `learning_events` | Historique d'apprentissage | Ajout seul ; idempotence `UNIQUE (enrollment_id, client_event_id)` ; FK composée (inscription, titulaire) ; cohérence avec l'édition suivie (module, leçon, version, bloc existant) ; pas d'événement futur ni antérieur à l'inscription ; inscription active |

### 2.2 Colonnes ajoutées

- `lesson_versions` : `submitted_at`, `published_by`, `content_sha256` ; contrainte d'états remplacée (`in_review` → `medical_review`) ; contraintes « approuvée ⇒ empreinte » et « publiée ⇒ éditeur » (`NOT VALID` : contrôlées sur toute ligne nouvelle ou modifiée sans réécrire l'historique).
- `programs` : `is_medical` (vrai par défaut).
- `student_progress` : `enrollment_id` (FK composée avec le titulaire), `lesson_version_id`, `last_block_id` ; unicité par inscription.

### 2.3 Règles imposées par PostgreSQL

| Règle | Mécanisme |
|---|---|
| Naissance en brouillon, sans trace de relecture | `trg_lesson_versions_10_insert`, `trg_program_releases_10_insert` |
| Transitions autorisées uniquement | `nl_transition_allowed` dans les déclencheurs de mise à jour |
| Acteur déclaré obligatoire | `nl_actor()` (`nl.actor_id`) |
| Habilitation dans la portée | `nl_check_transition_actor` (permission dans l'établissement du contenu ou globale) |
| Auto-approbation interdite | Acteur ≠ auteur ; relecteur d'édition ≠ auteurs des versions incluses ; contrainte d'origine `reviewed_by <> author_id` conservée |
| Programme médical non publiable sans validation | `nl_validate_release` : versions approuvées, empreintes intactes, au moins une source **active** par version |
| Références cohérentes entre versions | `nl_validate_release` : leçon ∈ module, version ∈ leçon, langue, juridiction, module privé, doublons, français obligatoire |
| Immuabilité | Versions et éditions figées hors brouillon ; sources figées ; suppression interdite ; empreinte revérifiée à la publication |
| Catalogue | `programs` / `modules` « published » seulement via une édition publiée ; nature, établissement et métier du programme figés après soumission |
| Historique des inscriptions préservé | Ancienne édition archivée, jamais supprimée ; inscription conservée et basculable vers une édition plus récente |

## 3. Résultats dans l'environnement de développement (PostgreSQL 16 réel)

| Contrôle | Résultat |
|---|---|
| Migration 0005 : montée / retour / remontée, signature de schéma identique avant et après l'aller-retour (`test_migration_up_down_up_restores_schema_exactly`) | ✅ |
| Chaîne Alembic 0001 → 0005 montée / descente / remontée (`tests/test_migrations.py`) | ✅ (révision pgvector : vérifiée en CI et sous Docker) |
| Nouveaux tests 0005 (`database/tests/test_learning_engine.py`) | ✅ 45/45 |
| Suite complète de la base (`database/tests`) | ✅ 100/100 |
| Fixture de démonstration CI rejouée deux fois (idempotente) via le workflow | ✅ |
| Parcours de la fixture des tests API rejoué en SQL (publication, catalogue, archivage) | ✅ |
| Ruff (lint + format), mypy | ✅ |
| Tests API, Vitest, Playwright, pile Docker | ✅ sur Windows (section 4) |

### Couverture des tests demandés

| Demande | Tests |
|---|---|
| Modèles | `test_models_match_schema` (API, contre la base réelle) — nouveaux modèles `ProgramRelease`, `Enrollment`, `LearningEvent`, `EditorialDecision` |
| Contraintes et déclencheurs | `test_new_tables_and_columns_exist`, `test_version_is_born_draft…`, `test_review_fields_cannot_be_forged`, `test_release_numbers_are_continuous` |
| Migration montée/retour/remontée | `test_migration_up_down_up_restores_schema_exactly`, `test_core_upgrade_downgrade_upgrade_on_postgres` |
| Versionnement | `test_approved_content_is_immutable_and_changes_need_a_new_version`, `test_new_release_archives_previous_and_supersedes_versions` |
| Immuabilité | `test_published_release_is_immutable`, `test_release_in_review_is_frozen`, `test_publication_detects_tampering_since_approval` |
| Transitions | `test_unauthorised_transitions_are_refused` (6 cas), `test_changes_requested_require_a_reason`, `test_transition_requires_a_declared_actor` |
| Auto-approbation | `test_author_cannot_approve_own_content_even_with_review_permission`, `test_release_reviewer_must_be_independent_from_included_authors` |
| Publication interdite | `test_programs_and_modules_cannot_be_published_directly`, `test_medical_release_requires_reviewed_versions`, `test_medical_release_requires_active_sources_for_every_lesson` |
| Isolation des établissements | `test_enrollment_organization_isolation`, cas « Module privé » de `test_incoherent_manifests_are_refused` |
| Cohérence des inscriptions | `test_enrollment_only_to_published_release_of_same_program`, `test_enrollment_history_is_preserved_across_releases`, `test_enrollment_status_transitions`, `test_progress_and_resume_point_stay_within_the_followed_release` |
| Idempotence | `test_learning_events_are_idempotent`, `test_incoherent_events_are_refused`, `test_learning_events_are_append_only` |
| Non-régression | Suites 1.1/1.2/2.1 de la base (55), tests API et e2e via `validate-phase2.2.1.ps1` |

### Tests existants adaptés (règles plus strictes, intention conservée)

Cinq tests du lot 1 inséraient directement des versions « approuvées » ou « publiées », ce que 0005 interdit désormais : ils passent maintenant par le workflow et vérifient la même règle (relecteur habilité, auteur ≠ relecteur, publication sans relecture refusée, une version publiée par langue). La fixture des tests API (`published_demo`) et la fixture CI (`e2e_demo_published.sql`) suivent le workflow complet et sont **non médicales** (`is_medical = false`) ; la fixture API archive ses éditions au lieu de les supprimer (historique immuable).

## 4. Résultats sur Windows (`validate-phase2.2.1.ps1`)

Machine de développement Windows, Docker Desktop, 19 novembre 2026, commit `02d6368` — journal `phase2.2.1-20261119-215818.log`.

**16/16 contrôles 2.2.1, 0 échec**, plus la non-régression complète :

| Ensemble | Résultat |
|---|---|
| Non-régression Phase 2.1 (`phase2.1-20261119-215819.log`), incluant qualité, audits, parcours d'identité | ✅ 42/42 |
| Non-régression Phase 1.2 (`phase1.2-20261119-220201.log`) | ✅ |
| pytest : base (dont les 45 tests de 0005), migrations Alembic avec pgvector, API (dont fixtures menées par le workflow) | ✅ 192 réussis |
| Vitest | ✅ 34/34 |
| Playwright + axe | ✅ 14 réussis, 1 ignoré (démonstration réservée à la CI) |
| Base de développement migrée en `0005_learning_engine`, 4 nouvelles tables, états à cinq valeurs, aucun `in_review` | ✅ |
| Pilote PAB : programme en brouillon, aucune édition, aucune version hors brouillon ; aucun programme ni édition publiés | ✅ |
| Sondes refusées par PostgreSQL (transaction annulée) : publication directe d'un programme, transition sans acteur, insertion d'une version « publiée », TRUNCATE du journal éditorial et de l'historique, décision forgée | ✅ 6/6 |
| Services Docker (postgres, redis, s3, api, web) | ✅ sains |

## 5. Limites

- La base fait confiance à l'acteur déclaré par l'application (`nl.actor_id`) ; un superutilisateur peut désactiver les déclencheurs (l'empreinte détecte alors la modification à la publication). Séparation des rôles PostgreSQL prévue au lot 2.5.
- `editorial_decisions` est protégé par un indicateur de transaction ; un client SQL qui le positionnerait lui-même pourrait y écrire (même limite, même remède).
- Les questions et simulations gardent `in_review` jusqu'au lot 2.3.
- Prérequis au niveau module seulement (D4) ; les prérequis ne sont pas encore contrôlés à l'ouverture d'une leçon (API 2.2.4).
- Aucun point d'accès API ni écran : sous-lots 2.2.2 et suivants.

## 6. Risques

| Risque | Niveau | Traitement |
|---|---|---|
| Module partagé entre programmes : publication bloquée tant qu'une autre édition publiée suit la version remplacée | Moyen | Décision D12 (coordination des éditions, ou versions par programme) avant 2.2.2 |
| Complexité des déclencheurs (maintenance, messages d'erreur à traduire côté API) | Moyen | Messages explicites, 45 tests ciblés, codes d'erreur SQL normalisés |
| Base de développement existante contenant des contenus publiés hors workflow | Faible | Aucun en développement (vérifié par le script) ; contraintes `NOT VALID` pour l'historique |
| Performance de la validation des manifestes JSON sur de gros programmes | Faible | Validation seulement aux transitions ; index à ajouter si besoin |

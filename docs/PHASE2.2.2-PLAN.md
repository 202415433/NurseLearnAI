# Plan technique du sous-lot 2.2.2 — API éditoriale

> **Statut : plan proposé, aucun développement commencé.** Démarrage sur votre autorisation.
> Références : [plan 2.2](PHASE2.2-PLAN.md) · [ADR-0009](adr/0009-editions-figees-et-workflow-editorial.md) · [ADR-0010](adr/0010-versions-de-modules-par-edition.md) (D12) · [ADR-0007](adr/0007-identite-acces-sessions.md) · [validation 2.2.1](VALIDATION-PHASE2.2.1.md)

## 0. Découpage proposé

| Étape | Contenu | Pourquoi d'abord |
|---|---|---|
| **2.2.2-a** | Migration additive `0006_module_versions` (ADR-0010) + codes d'erreur normalisés + tests PostgreSQL | L'API manipule des versions de modules : le modèle doit exister et être verrouillé en base avant |
| **2.2.2-b** | API éditoriale (ce plan, § 1 à § 9) | Repose sur 0006 |
| **2.2.2-c** | Adaptation du catalogue public (lecture par l'édition publiée) | Rendue nécessaire par la suppression de l'index « une version publiée » |

Estimation : 2-a ≈ 3 jours, 2-b ≈ 6 à 8 jours, 2-c ≈ 1 à 2 jours.

## 1. Adaptations de données nécessaires pour D12 (étape 2.2.2-a)

| # | Adaptation | Type |
|---|---|---|
| A1 | Table `module_versions` (module, n° continu, état à cinq valeurs, auteur, relecteur, dates, empreinte, résumé des changements) | Nouvelle table |
| A2 | Table `module_version_lessons` (leçon, langue, version exacte, position, obligatoire) avec clés composées vers `module_versions (id, module_id)`, `lessons (id, module_id)`, `lesson_versions (id, lesson_id)` | Nouvelle table |
| A3 | Contraintes d'unicité support `lessons (id, module_id)` et `lesson_versions (id, lesson_id)` | Contraintes ajoutées (aucune donnée modifiée) |
| A4 | Tables `program_release_modules` et `program_release_prerequisites` (prérequis figés par édition, sans cycle) | Nouvelles tables |
| A5 | `program_releases.structure` (`manifest_v1` pour l'existant, `module_versions` imposé pour toute nouvelle édition) | Colonne ajoutée |
| A6 | Fonction `nl_release_lessons(release)` et redéfinition des fonctions 0005 qui lisent le manifeste | Fonctions |
| A7 | Suppression de `ux_lesson_versions_one_published` et de l'archivage implicite / blocage croisé ; archivage d'une version refusé tant qu'une édition publiée la cite | Index et règles |
| A8 | Source médicale : `status = 'active'` **et** `last_verified_at` renseigné pour approuver une édition médicale | Règle renforcée |
| A9 | `learning_events.module_version_id`, `student_progress.module_version_id` (cohérents avec l'édition suivie) | Colonnes ajoutées |
| A10 | Codes d'erreur stables : chaque `RAISE` des règles éditoriales porte `HINT = 'nl:<code>'` (ex. `nl:self_approval`, `nl:transition_forbidden`, `nl:missing_source`) pour une traduction fiable par l'API | Fonctions redéfinies |

Les questions et simulations gardent `in_review` (lot 2.3). Les éditions `manifest_v1` restent valides et lisibles.

## 2. Authentification et RBAC

- Session opaque du lot 2.1 (`Authorization: Bearer`, via le serveur Next.js) ; toutes les routes éditoriales exigent `require_auth`, et **toute transition ou modification** exige `require_sensitive` (activité dans les 30 dernières minutes).
- Permissions vérifiées dans l'API **et** en base : `content:create` (rédiger, soumettre, abandonner), `content:review` (approuver, demander des modifications), `content:publish` (publier une édition), `content:review` ou `content:publish` (archiver).
- Portée : établissement du module ou du programme ; un contenu hors périmètre répond **404** (ne rien divulguer), un contenu visible mais une action non permise répond **403**.
- Dans la même transaction que l'écriture : `SELECT set_config('nl.actor_id', <utilisateur de la session>, true)` — jamais une valeur du client.

## 3. Points d'accès (préfixe `/api/v1/editorial`)

| Méthode et chemin | Rôle | Effet |
|---|---|---|
| `GET /programs` · `GET /modules` · `GET /lessons/{id}` | auteur, relecteur (portée) | Listes et détails éditoriaux, brouillons compris |
| `POST /programs` · `PATCH /programs/{id}` | `content:create` | Programme en brouillon ; nature médicale, établissement et métier figés dès la première édition soumise (règle 0005) |
| `POST /modules` · `PATCH /modules/{id}` · `POST /modules/{id}/lessons` | `content:create` | Module et leçons (structure de travail) |
| `POST /lessons/{id}/versions` | `content:create` | Nouvelle version en brouillon (vide ou copie d'une version existante : `from_version_id`) |
| `GET/PATCH /lesson-versions/{id}` | `content:create` | Lecture ; modification **uniquement en brouillon** (`If-Match` : empreinte du brouillon) |
| `PUT /lesson-versions/{id}/sources` | `content:create` | Sources citées (brouillon seulement) |
| `POST /modules/{id}/versions` · `PATCH /module-versions/{id}` | `content:create` | Version de module : leçons, langues, versions exactes (D12) |
| `POST /programs/{id}/releases` · `PATCH /releases/{id}` | `content:create` | Édition : versions de modules, positions, obligatoire, prérequis, résumé |
| `POST /{kind}/{id}/submit` | `content:create` | `draft → medical_review` |
| `POST /{kind}/{id}/request-changes` | `content:review` | `medical_review → draft`, motif obligatoire |
| `POST /{kind}/{id}/approve` | `content:review`, ≠ auteur | `medical_review → approved` (+ `next_review_due` pour une leçon) |
| `POST /releases/{id}/publish` | `content:publish` | `approved → published` ; publie les versions de modules et de leçons approuvées qu'elle cite |
| `POST /{kind}/{id}/archive` · `POST /{kind}/{id}/abandon` | relecteur ou éditeur · auteur | `→ archived` (refusé si une édition publiée cite encore la version) |
| `GET /review-queue` | `content:review` | Contenus en `medical_review` dans la portée, du plus ancien au plus récent |
| `GET /{kind}/{id}/decisions` | auteur, relecteur | Journal `editorial_decisions` |
| `GET /{kind}/{id}/diff?against={id}` | auteur, relecteur | Différences bloc par bloc (leçons) ou structure (modules, éditions) |
| `GET/POST /references` · `POST /references/{id}/verify` | `content:create` · `content:review` | Sources ; vérification datée par un relecteur (A8) |

`{kind}` ∈ `lesson-versions`, `module-versions`, `releases`. Aucune route ne publie une leçon ou un module isolément : la publication passe par une édition.

## 4. Contrats Pydantic (principaux)

- Base `Strict` (`extra="forbid"`, comme au lot 2.1) ; identifiants `UUID` ; textes bornés.
- `Block` : union discriminée par `type` (`heading`, `paragraph`, `list`, `table`, `callout`, `definition`, `steps`, `key_points`, `image`, `video`, `document`, `reference`), `id` stable (`^[a-z0-9_-]{1,64}$`, unique dans la version), texte enrichi limité (gras, italique, liens `https` ou internes), **aucun HTML brut**, 300 blocs au plus.
- Entrées : `LessonVersionDraftIn {title, body: {schema: "blocks/v1", blocks}, change_summary?, jurisdiction_id?}`, `ModuleVersionDraftIn {lessons: [{lesson_id, position, required, versions: {fr, en?}}], change_summary}`, `ReleaseDraftIn {modules: [{module_version_id, position, required}], prerequisites: [{module_version_id, requires}], change_summary}`, `RequestChangesIn {comment (5–4000)}`, `ApproveIn {next_review_due?}`, `ArchiveIn {reason}`.
- Sorties : `LessonVersionOut`, `ModuleVersionOut`, `ReleaseOut` (état, auteur, relecteur, dates, empreinte, éditions qui la citent), `DecisionOut`, `ReviewQueueItem`, `DiffOut`.

## 5. Brouillons, versionnement, soumission, approbation, publication

- **Brouillons** : création libre dans la portée ; modification avec contrôle de concurrence (`If-Match`) ; suppression d'un brouillon jamais cité.
- **Versionnement** : une version approuvée ou publiée n'est jamais modifiée ; « modifier » = `POST …/versions` avec `from_version_id` (copie en brouillon, numéro suivant). Aucune édition ne bascule implicitement sur une nouvelle version.
- **Soumission** : l'API valide le schéma de blocs et la complétude, puis la base valide les liens et l'état des versions incluses.
- **Approbation indépendante** : refus si le relecteur est l'auteur, ou l'auteur d'une version incluse (contrôles API + base) ; échéance de révision obligatoire pour une leçon.
- **Publication et archivage** : publication de l'édition seulement ; archivage refusé tant qu'une édition publiée cite la version ; l'édition publiée précédente du même programme est archivée, les inscriptions restent sur leur édition (bascule explicite au sous-lot 2.2.4).

## 6. Journal d'audit

`audit_logs` (immuable, chaîné) pour chaque action : `content.created`, `content.updated`, `content.submitted`, `content.changes_requested`, `content.approved`, `content.published`, `content.archived`, `release.*`, `reference.verified`, avec type, identifiant, portée et empreinte ; **jamais** le corps du contenu ni de donnée personnelle superflue. `editorial_decisions` est alimenté par la base.

## 7. Isolation des établissements

404 pour toute ressource d'un autre établissement (identifiant direct, listes, filtres, file de relecture, journal, différences, modifications) ; filtres SQL appliqués côté serveur à partir de la session, jamais d'un paramètre client ; un module privé n'entre que dans une édition de son établissement (API + base).

## 8. Gestion des erreurs (`application/problem+json`)

| Origine | Statut | `type` |
|---|---|---|
| Validation Pydantic, schéma de blocs | 422 | `invalid-input`, `invalid-block` |
| `HINT nl:transition_forbidden`, `nl:not_draft`, `nl:immutable` | 409 | `editorial-state-conflict` |
| `nl:self_approval`, `nl:reviewer_not_independent` | 403 | `independence-required` |
| `insufficient_privilege` (habilitation en base) | 403 | `forbidden` |
| `nl:missing_source`, `nl:unapproved_component`, `nl:incoherent_reference` | 422 | `publication-requirements` (avec la liste des éléments en cause) |
| `If-Match` absent / périmé | 428 / 412 | `precondition-required` / `stale-draft` |
| Opération sensible après 30 min d'inactivité | 403 | `reauthentication-required` (lot 2.1) |

Le texte SQL brut n'est jamais renvoyé ; il est journalisé côté serveur sans contenu sensible.

## 9. Tests

| Niveau | Contenu |
|---|---|
| PostgreSQL (2.2.2-a) | 0006 montée/retour/remontée ; deux programmes sur deux versions d'un module ; publication indépendante ; inscriptions conservées ; liens incohérents refusés par clés étrangères ; éditions `manifest_v1` toujours valides ; codes `HINT` |
| Intégration API | Parcours complet par ressource ; demande de modifications ; nouvelle version après approbation ; publication d'édition ; archivage refusé si cité |
| Sécurité | Matrice RBAC complète ; auto-approbation et non-indépendance ; étudiant, formateur d'un autre établissement, administrateur d'établissement qui publie ; acteur jamais lu du client ; 30 minutes ; HTML et liens `javascript:` refusés ; tailles maximales ; limites de débit |
| Isolation | Identifiant direct, listes, filtres, file de relecture, journal, différences, modifications |
| Concurrence | `If-Match` ; double soumission → 409 ; transitions simultanées |
| Non-régression | Catalogue public sans brouillon ; `validate-phase2.2.1.ps1` ; nouveau `validate-phase2.2.2.ps1` ; CI 7 jobs |

## 10. Critères d'acceptation du sous-lot

1. 0006 appliquée et réversible sur PostgreSQL réel ; aucune migration historique modifiée.
2. Deux programmes suivent deux versions différentes d'un même module ; la publication de l'un ne bloque ni ne modifie l'autre.
3. Toutes les transitions passent par l'API avec les contrôles d'habilitation, d'indépendance et de portée ; aucune n'est possible sans session sensible.
4. Aucune modification d'un contenu approuvé ; toute évolution crée une nouvelle version soumise à relecture.
5. Erreurs stables et traduites, sans fuite de SQL.
6. Audit complet, sans secret ni contenu.
7. Isolation vérifiée sur toutes les routes.
8. Aucun contenu médical publié ; pilote PAB en brouillon ; non-régression complète.

## 11. Risques

| Risque | Niveau | Atténuation |
|---|---|---|
| Complexité de 0006 (redéfinition de fonctions de 0005) | Moyen | Étape dédiée 2.2.2-a, signature de schéma, retour arrière testé |
| Catalogue affichant plusieurs versions publiées d'une même leçon avant 2.2.2-c | Moyen | 2.2.2-c dans le même sous-lot ; aucune donnée concernée tant que 0006 n'est pas publiée en production |
| Ergonomie de l'édition par formulaires | Faible | Interface au sous-lot 2.2.6 |
| Confiance dans l'acteur déclaré (D14) | Moyen avant production | Rôles PostgreSQL séparés au lot 2.5 |

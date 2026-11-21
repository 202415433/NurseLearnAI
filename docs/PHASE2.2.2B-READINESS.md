# Préparation de l'étape 2.2.2-b — API éditoriale (plan d'implémentation détaillé)

> État : **plan prêt, développement non commencé**. L'étape 2.2.2-b démarre uniquement sur votre autorisation.
> Références : [plan 2.2.2](PHASE2.2.2-PLAN.md) · [ADR-0009](adr/0009-editions-figees-et-workflow-editorial.md) · [ADR-0010](adr/0010-versions-de-modules-par-edition.md) · [ADR-0007](adr/0007-identite-acces-sessions.md) · [validation 2.2.2-a](VALIDATION-PHASE2.2.2A.md)

## 0. Prérequis (tous remplis)

| Prérequis | État |
|---|---|
| Migration 0006 (`0006_independent_versions`) intégrée dans `main` | ✅ PR #10, `b149e12`, CI 7/7 |
| Plan 2.2.2 et ADR-0010 dans `main` | ✅ PR #9 |
| Validation Windows de 2.2.2-a | ✅ pytest 214, non-régression complète, 71 tables |
| Authentification, sessions, RBAC, audit (Phase 2.1) | ✅ `require_auth`, `require_sensitive`, `AuthContext.can()`, `app/core/audit.py` |

## 1. Principes non négociables

1. **La base reste l'autorité** : toute transition s'exécute dans une transaction qui déclare `nl.actor_id` = utilisateur de la session (jamais une valeur du client) ; PostgreSQL revérifie habilitation, portée, indépendance, immuabilité et sources. L'API contrôle **avant** (messages clairs, 403/404 corrects) et la base **après** : aucun contournement possible, même si le frontend ou l'API ont un défaut.
2. **Aucune route de contournement** : pas de publication isolée d'une version de leçon ou de module, pas de « force », pas de paramètre de dérogation, pas d'écriture directe de `reviewed_by`, `verified_by`, `published_by`, ni de champ d'état dans un `PATCH`.
3. **Contrats stricts** : modèles Pydantic `extra="forbid"`, types et bornes explicites ; tout champ inattendu → 422.
4. **Opérations sensibles** : toute écriture éditoriale exige `require_sensitive` (activité de moins de 30 minutes, lot 2.1).
5. **Isolation** : ressource hors périmètre → **404** (rien n'est divulgué) ; ressource visible mais action non permise → **403**.

## 2. Architecture du code

```
services/api/app/domains/editorial/
  schemas.py         contrats Pydantic (entrées/sorties), union de blocs blocks/v1
  blocks.py          validation du corps des leçons (types, identifiants, liens, tailles)
  scope.py           visibilité et portée (établissement du module ou du programme), 404/403
  tx.py              transaction éditoriale : set_config nl.actor_id / nl.decision_comment
  errors.py          traduction des erreurs PostgreSQL → problem+json (HINT nl:* et liste fermée 0005)
  diff.py            comparaison bloc par bloc et structurelle (difflib, bibliothèque standard)
  queries.py         lectures (file de relecture, journal, contenu d'édition via nl_release_lessons)
  routes_content.py  programmes, modules, leçons (structure de travail)
  routes_lessons.py  versions de leçon et sources citées
  routes_modules.py  versions de modules
  routes_releases.py éditions, prérequis, publication
  routes_review.py   transitions, file de relecture, journal, comparaison
  routes_sources.py  références médicales et vérification
```

Aucune nouvelle dépendance Python. Préfixe : `/api/v1/editorial`.

## 3. Endpoints

Accès : **C** = `content:create`, **R** = `content:review`, **P** = `content:publish`, dans la portée (établissement du contenu, ou global). ✱ = opération sensible (`require_sensitive`).

### 3.1 Structure de travail — brouillons (1)

| # | Méthode et chemin | Accès | Réponse | Notes |
|---|---|---|---|---|
| 1 | `GET /programs` · `GET /programs/{id}` | C ou R | 200 | Brouillons compris, filtrés par portée |
| 2 | `POST /programs` | C ✱ | 201 | `is_medical` vrai par défaut ; établissement limité à ceux où l'utilisateur détient C |
| 3 | `PATCH /programs/{id}` | C ✱ | 200 | Nature médicale, établissement, métier figés dès la 1re édition soumise (base) → 409 |
| 4 | `GET/POST /modules` · `PATCH /modules/{id}` | C (R en lecture) ✱ | 200/201 | Module public ⇒ C global |
| 5 | `POST /modules/{id}/lessons` · `PATCH /lessons/{id}` | C ✱ | 201/200 | Code, position, type, durée |

### 3.2 Versions de leçon (2)

| # | Méthode et chemin | Accès | Réponse | Notes |
|---|---|---|---|---|
| 6 | `POST /lessons/{id}/versions` | C ✱ | 201 | Brouillon vide ou copie (`from_version_id`) ; numéro suivant revérifié par la base |
| 7 | `GET /lesson-versions/{id}` | C ou R | 200 + `ETag` | `ETag` = empreinte du brouillon |
| 8 | `PATCH /lesson-versions/{id}` | C ✱ | 200 | **Brouillon seulement** ; `If-Match` obligatoire (428/412) ; titre, corps, résumé, juridiction |
| 9 | `PUT /lesson-versions/{id}/sources` | C ✱ | 200 | Liste complète des sources citées (brouillon seulement) |
| 10 | `DELETE /lesson-versions/{id}` | C ✱ | 204 | Brouillon jamais cité (clé étrangère + déclencheur) |

### 3.3 Versions de modules (3)

| # | Méthode et chemin | Accès | Réponse | Notes |
|---|---|---|---|---|
| 11 | `POST /modules/{id}/versions` | C ✱ | 201 | Leçons, positions, obligatoire, version exacte par langue (FR obligatoire), ou copie `from_version_id` |
| 12 | `GET /module-versions/{id}` · `PATCH /module-versions/{id}` | C ✱ | 200 | `PATCH` remplace la liste en brouillon (une transaction), `If-Match` |
| 13 | `DELETE /module-versions/{id}` | C ✱ | 204 | Brouillon non cité |

### 3.4 Éditions de programme (4)

| # | Méthode et chemin | Accès | Réponse | Notes |
|---|---|---|---|---|
| 14 | `POST /programs/{id}/releases` | C ✱ | 201 | Versions de modules + positions + obligatoire + prérequis ; `structure = module_versions` imposée |
| 15 | `GET /releases/{id}` · `PATCH /releases/{id}` | C ✱ | 200 | Contenu lu via `nl_release_lessons()` (deux structures) ; `PATCH` brouillon seulement |
| 16 | `GET /programs/{id}/releases` | C ou R | 200 | Historique des éditions et nombre d'inscriptions rattachées |

### 3.5 Transitions (5 à 9)

`{kind}` ∈ `lesson-versions`, `module-versions`, `releases`.

| # | Méthode et chemin | Accès | Transition | Corps |
|---|---|---|---|---|
| 17 | `POST /{kind}/{id}/submit` | C ✱ | draft → medical_review | — |
| 18 | `POST /{kind}/{id}/request-changes` | R ✱, ≠ auteur | medical_review → draft | `{comment}` 5–4000 caractères |
| 19 | `POST /{kind}/{id}/approve` | R ✱, ≠ auteur, indépendant | medical_review → approved | `{next_review_due}` obligatoire pour une leçon (date future) |
| 20 | `POST /releases/{id}/publish` | P ✱ | approved → published | Publie les versions approuvées citées par l'édition, rien d'autre |
| 21 | `POST /{kind}/{id}/archive` | R ou P ✱ | approved/published → archived | `{reason}` ; 409 si une édition publiée cite la version |
| 22 | `POST /{kind}/{id}/abandon` | C ✱ | draft → archived | `{reason}` |

**Aucune route `publish`** pour `lesson-versions` ni `module-versions` : la publication passe exclusivement par l'édition.

### 3.6 Relecture, journal, comparaison, sources (10 à 13)

| # | Méthode et chemin | Accès | Réponse |
|---|---|---|---|
| 23 | `GET /review-queue?kind=&organization_id=&cursor=` | R | Éléments en `medical_review` de la portée, du plus ancien au plus récent ; ceux dont l'utilisateur est auteur sont marqués « non relisible » |
| 24 | `GET /{kind}/{id}/decisions` | C ou R | `editorial_decisions` : acteur, décision, date, empreinte, motif |
| 25 | `GET /{kind}/{id}/diff?against={id}` | C ou R | Leçons : blocs ajoutés, supprimés, modifiés (par identifiant) ; modules : leçons et versions ; éditions : versions de modules, ordre, prérequis |
| 26 | `GET /references` · `POST /references` | C | Source créée **non vérifiée** |
| 27 | `POST /references/{id}/verify` | R global ✱ | Pose `last_verified_at` ; la base renseigne `verified_by` |
| 28 | `POST /references/{id}/withdraw` | R ✱ | `status = withdrawn` : les éditions déjà publiées restent, les nouvelles approbations la refusent |

## 4. Contrats de données (Pydantic)

- Base `Strict` : `extra="forbid"`, `str_strip_whitespace=True` ; `UUID` partout ; dates avec fuseau.
- **Blocs `blocks/v1`** (union discriminée par `type`) : `heading{level 2–4, text ≤ 200}`, `paragraph{text ≤ 4000}`, `list{ordered, items 1–50}`, `table{headers, rows ≤ 50 × 10}`, `callout{tone: info|important|safety, text}`, `definition{term, text}`, `steps{items 1–30}`, `key_points{items 1–10}`, `image|video|document{resource_id}`, `reference{reference_id, locator?}`. Identifiant `^[a-z0-9][a-z0-9_-]{0,63}$`, unique dans la version ; 1–300 blocs ; texte enrichi limité (gras, italique, liens `https://` ou internes) ; **aucun HTML**, refus de `javascript:`, `data:` et des caractères de contrôle ; corps ≤ 256 Ko.
- **Règle médicale côté API**, en plus de la base : un bloc `steps` ou `callout{tone: safety}` exige au moins une source citée avant soumission.
- Entrées : `ProgramIn`, `ProgramPatch`, `ModuleIn`, `LessonIn`, `LessonVersionCreate{from_version_id?, locale, jurisdiction_id?}`, `LessonVersionPatch{title?, body?, change_summary?}`, `SourcesPut{references ≤ 30}`, `ModuleVersionIn{lessons 1–100: {lesson_id, position, required, versions: {fr, en?}}, change_summary?, from_version_id?}`, `ReleaseIn{modules 1–50: {module_version_id, position, required}, prerequisites ≤ 200: {module_id, requires}, change_summary}`, `RequestChangesIn{comment}`, `ApproveIn{next_review_due?}`, `ArchiveIn{reason}`, `ReferenceIn{kind, title, url https, publisher?, published_on?, locale?}`.
- Sorties : `LessonVersionOut`, `ModuleVersionOut`, `ReleaseOut{structure, modules, prerequisites, status, auteur, relecteur, dates, content_sha256, can}`, `DecisionOut`, `ReviewQueueItem`, `DiffOut`, `ReferenceOut{verified, verified_at}`. Le champ `can` est **indicatif** (affichage) et ne remplace jamais les contrôles serveur.

## 5. Transactions et erreurs `application/problem+json` (14)

`tx.py` : une requête = une transaction ; `set_config('nl.actor_id', <session>, true)` (+ `nl.decision_comment`) puis l'écriture.

| Origine | Statut | `type` (`https://nurselearn.ai/problems/…`) |
|---|---|---|
| Pydantic, `blocks.py` | 422 | `invalid-input`, `invalid-block` (chemin du bloc fautif) |
| `nl:transition_forbidden`, `nl:not_draft`, `nl:immutable`, `nl:in_use`, `nl:history` | 409 | `editorial-state-conflict` (`code` = HINT) |
| `nl:reviewer_not_independent`, auto-approbation | 403 | `independence-required` |
| `insufficient_privilege` (dont `nl:reviewer_required`) | 403 | `forbidden` |
| `nl:missing_source`, `nl:unapproved_component`, `nl:unsubmitted_component`, `nl:tampered`, `nl:incoherent_reference`, `nl:empty_*`, `nl:missing_base_locale`, `nl:prerequisite_cycle`, `nl:organization_scope` | 422 | `publication-requirements` (éléments en cause) |
| `foreign_key_violation` | 422 | `invalid-reference` |
| `unique_violation` | 409 | `conflict` |
| `If-Match` absent / périmé | 428 / 412 | `precondition-required` / `stale-draft` |
| Session inactive depuis plus de 30 min | 403 | `reauthentication-required` |
| Erreur non répertoriée | 500 | `internal` — **jamais** de texte SQL renvoyé |

Les erreurs des fonctions de 0005 non redéfinies (sans `HINT`) sont traduites par SQLSTATE et **liste fermée** de préfixes ; un test parcourt `database/schema/*.sql` et échoue si un `RAISE EXCEPTION` éditorial n'est pas couvert.

## 6. Permissions et isolation (15)

| Action | Étudiant | Formateur (établissement) | Relecteur médical (global) | Admin. d'établissement | Admin. plateforme |
|---|---|---|---|---|---|
| Lire les brouillons | — | son établissement | global | — | global |
| Rédiger, soumettre, abandonner | — | contenus de son établissement | — | — | global |
| Approuver, demander des modifications | — | — | ✅ jamais ses contenus ni une édition qui en contient | — | ✅ mêmes restrictions |
| Publier une édition | — | — | ✅ | — | ✅ |
| Vérifier une source | — | — | ✅ | — | ✅ |

Filtres de portée appliqués en SQL à partir de la session ; aucun paramètre client n'élargit la portée ; 404 sur toutes les routes à identifiant hors portée (direct, listes, filtres, file, journal, différences, modifications).

## 7. Audit (12)

`audit_logs` (immuable, lot 2.1) pour chaque écriture : `content.created`, `content.updated`, `content.deleted`, `content.submitted`, `content.changes_requested`, `content.approved`, `content.published`, `content.archived`, `content.abandoned`, `release.created`, `reference.created`, `reference.verified`, `reference.withdrawn` — type, identifiant, portée, empreinte ; **jamais** le corps du contenu ni de secret. `editorial_decisions` reste alimenté par la base.

## 8. Stratégie de tests (16)

| Niveau | Contenu |
|---|---|
| Unitaires | `blocks.py` (types, identifiants, HTML, `javascript:`, tailles), `diff.py`, couverture de `errors.py` |
| Intégration API (PostgreSQL + Redis réels) | Parcours complet par ressource ; modifications demandées ; nouvelle version après approbation ; deux programmes sur deux versions d'un module ; archivage refusé si cité ; éditions historiques lisibles |
| Sécurité | Matrice §6 complète ; auto-approbation, non-indépendance ; étudiant, formateur d'un autre établissement, admin d'établissement qui publie ; acteur jamais lu du client ; champs d'état ou de relecture refusés dans les `PATCH` ; 30 minutes ; limites de débit |
| Isolation | Toutes les routes à identifiant, listes, filtres, file, journal, différences |
| Concurrence | `If-Match` ; double soumission → 409 ; deux approbations simultanées → une seule réussit |
| Non-régression | Catalogue public sans brouillon ; identité ; `validate-phase2.2.2a.ps1` ; nouveau `validate-phase2.2.2b.ps1` ; CI 7 jobs |

Relecteurs et auteurs fictifs **uniquement dans les bases de test** ; programmes de démonstration **non médicaux** ; aucune vérification de source simulée hors tests.

## 9. Découpage de 2.2.2-b (≈ 6 à 8 jours)

| Pas | Contenu | Sortie |
|---|---|---|
| b1 | `tx.py`, `errors.py`, `scope.py`, schémas de base, structure de travail (§3.1) | Tests d'isolation de base |
| b2 | Versions de leçon, blocs, sources, `ETag` (§3.2, sources) | Tests blocs et concurrence |
| b3 | Versions de modules, éditions, prérequis (§3.3, §3.4) | Tests D12 par l'API |
| b4 | Transitions, file de relecture, journal, comparaison, audit (§3.5, §3.6) | Matrice de sécurité complète |
| b5 | Référence API, `validate-phase2.2.2b.ps1`, documentation de validation | Validation Windows |

## 10. Critères d'acceptation

1. Chaque transition n'est possible que par l'API, avec session sensible, habilitation, portée et indépendance ; la base la revérifie.
2. Aucun auteur n'approuve son contenu ; aucun relecteur n'approuve une édition contenant ses propres versions.
3. Aucune publication sans édition approuvée ; programme médical : sources actives **et vérifiées** pour chaque leçon.
4. Aucune modification d'un contenu soumis, approuvé ou publié ; toute évolution = nouvelle version, nouvelle relecture.
5. Deux programmes publient deux versions d'un même module sans interférence ; inscriptions inchangées.
6. Erreurs `problem+json` stables, sans SQL ; 404 hors portée sur toutes les routes.
7. Audit complet sans contenu ni secret ; journal des décisions consultable.
8. Tout champ inattendu ou interdit → 422.
9. Aucun contenu médical publié ; pilote PAB en brouillon ; non-régression complète ; CI 7/7.

## 11. Risques restants

| Risque | Niveau | Atténuation |
|---|---|---|
| Traduction incomplète des erreurs de 0005 (sans `HINT`) | Moyen | Liste fermée + test de couverture des `RAISE` |
| Complexité de la matrice permissions × portée | Moyen | Tests paramétrés exhaustifs ; base en dernier rempart |
| Catalogue public lisant plusieurs versions publiées | Moyen | Étape 2.2.2-c, aussitôt après 2.2.2-b |
| Confiance dans l'acteur déclaré (D14) | Moyen avant production | Rôles PostgreSQL séparés au lot 2.5 |
| Surface d'attaque du contenu riche | Faible | Pas de HTML, liste blanche, tailles bornées, rendu échappé |

## 12. Décisions

| # | Sujet | Proposition | Bloquant pour développer 2.2.2-b ? |
|---|---|---|---|
| B1 | Erreurs 0005 sans `HINT` | Liste fermée + test de couverture (pas de nouvelle migration) | Non |
| B2 | Qui rédige le **contenu public** (catalogue) | `content:create` **global** : administrateur de la plateforme, ou formateur doté d'un rôle global attribué par l'administrateur (commande d'exploitation journalisée) | Non pour le développement ; **oui** pour rédiger le pilote |
| B3 | Concurrence | `ETag` = empreinte du brouillon ; `If-Match` obligatoire sur `PATCH` | Non |
| B4 | Publication isolée d'une version de leçon ou de module | Non exposée (édition uniquement) | Non (déjà acté) |
| D7 | Antivirus des médias | Lot 2.2.3 | Non |
| D9, D10 | MFA des relecteurs ; relecteur médical qualifié | Avant toute publication réelle | Non pour 2.2.2-b ; **oui** pour publier le pilote |
| D14 | Rôles PostgreSQL séparés | Lot 2.5 | Non (requis avant production) |

**Aucune décision ne bloque le développement de 2.2.2-b.** B2, D9 et D10 bloquent seulement la publication réelle du pilote.

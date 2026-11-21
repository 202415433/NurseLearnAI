# Validation — Étape 2.2.2-b : API éditoriale

Branche `phase-2.2.2b-editorial-api`, partie de `main` = `000a1f3` (fusion de #11). **Aucune migration
ajoutée ni modifiée** : l'API s'appuie sur les règles PostgreSQL de 0005 et 0006, qu'elle ne contourne
jamais. Alembic reste en tête à `0006_independent_versions`. Le modèle compte toujours 71 tables.

## 1. Architecture

```
services/api/app/domains/editorial/
  router.py            agrège les routeurs sous /api/v1/editorial
  routes_content.py    programmes, modules, leçons (structure de travail)
  routes_lessons.py    versions de leçon : brouillons, ETag / If-Match, sources citées
  routes_modules.py    versions de modules (D12)
  routes_releases.py   éditions de programme, publication contrôlée
  routes_review.py     soumission, relecture, approbation, archivage, abandon, file, historique, diff
  routes_sources.py    sources médicales : enregistrement, vérification, retrait
  scope.py             visibilité, 404 hors portée, 403 action interdite, verrouillage FOR UPDATE
  tx.py                transaction éditoriale (nl.actor_id issu de la session), audit, ETag
  errors.py            traduction PostgreSQL → problem+json à code stable
  queries.py           contrôles de publication (chaîne des décisions, échéances, sources)
  blocks.py            schéma de contenu blocks/v1 (sans HTML)
  schemas.py           contrats Pydantic stricts (extra="forbid")
  diff.py              comparaison de versions
```

Chaque écriture suit le même chemin :

1. `require_sensitive` : session Bearer valide, activité dans les 30 dernières minutes.
2. `load(…, lock=True)` : l'élément est chargé et verrouillé (`FOR UPDATE`). S'il est hors portée, la
   réponse est **404**.
3. `ensure_can(...)` : une action non permise sur un élément visible répond **403**.
4. Pré-contrôles explicites : état, auteur ≠ relecteur, date de révision, sources.
5. `editorial_tx` : `set_config('nl.actor_id', <utilisateur de la session>, true)`, puis le motif dans
   `nl.decision_comment`. Les déclencheurs de 0005 et 0006 revérifient alors habilitation, portée,
   indépendance, immuabilité, empreintes et sources.
6. `record(...)` : audit dans la même transaction, sans contenu.

Le journal `editorial_decisions` n'est jamais écrit par l'API ; seuls les déclencheurs l'alimentent.

Configuration ajoutée : `medical_publication_enabled: bool = False`. La publication **réelle** d'un
programme médical reste refusée (403 `medical-publication-disabled`) tant que les conditions de
production ne sont pas réunies : MFA des relecteurs (D9), relecteur qualifié (D10) et textes juridiques.

## 2. Routes : 37 opérations (les 28 groupes du plan)

Toutes les routes exigent une session. Toutes les écritures exigent une session sensible (< 30 min).

| Groupe | Opérations | Permission (portée = établissement du contenu, ou plateforme) |
|---|---|---|
| Programmes | `GET /programs`, `POST /programs`, `GET /programs/{id}`, `PATCH /programs/{id}` | lecture : `content:create\|review\|publish` ; écriture : `content:create` |
| Modules | `GET /modules`, `POST /modules`, `GET /modules/{id}`, `PATCH /modules/{id}` | idem |
| Leçons | `POST /modules/{id}/lessons`, `GET /lessons/{id}`, `PATCH /lessons/{id}` | idem |
| Versions de leçon | `POST /lessons/{id}/versions` ; `GET`, `PATCH` (If-Match) et `DELETE` (If-Match) sur `/lesson-versions/{id}` ; `PUT /lesson-versions/{id}/sources` (If-Match) | `content:create` |
| Versions de module | `POST /modules/{id}/versions` ; `GET`, `PATCH` (If-Match) et `DELETE` (If-Match) sur `/module-versions/{id}` | `content:create` |
| Éditions | `GET /programs/{id}/releases`, `POST /programs/{id}/releases`, `GET /releases/{id}`, `PATCH /releases/{id}` (If-Match) | `content:create` |
| Publication | `POST /releases/{id}/publish` — **seule voie de publication** | `content:publish` |
| Transitions | `POST /{kind}/{id}/submit`, `…/request-changes`, `…/approve`, `…/archive`, `…/abandon`, avec `kind` ∈ `lesson-versions`, `module-versions`, `releases` | soumettre ou abandonner : `content:create` ; renvoyer ou approuver : `content:review`, auteur exclu ; archiver : `content:review` ou `content:publish` |
| Relecture | `GET /review-queue`, `GET /{kind}/{id}/decisions`, `GET /{kind}/{id}/diff?against=` | file : `content:review` ; le reste : lecture |
| Sources | `GET /references`, `POST /references`, `POST /references/{id}/verify`, `POST /references/{id}/withdraw` | enregistrer : `content:create` ; vérifier ou retirer : `content:review` **plateforme** |

Il n'existe **aucune** route de publication directe d'une leçon ou d'un module. Le test
`test_route_inventory_and_no_direct_publication` le garantit.

`DELETE` ne s'applique qu'à un brouillon **jamais soumis**, qui n'a donc aucune décision, et qu'aucune
version de module ou édition ne cite. Un brouillon renvoyé avec demande de modifications garde son
historique : il s'abandonne (archivage journalisé), il ne se supprime pas.

## 3. Contrats

Les contrats Pydantic sont stricts : `extra="forbid"` et longueurs bornées. Aucun champ d'état,
d'auteur, de relecteur, d'empreinte ou de date de vérification n'est accepté. L'acteur vient
**toujours** de la session.

- **Contenu `blocks/v1`** :
  - types : heading, paragraph, list, table, callout, definition, steps, key_points, image, video,
    document, reference ;
  - au plus 300 blocs, avec des identifiants stables et uniques ;
  - HTML, caractères de contrôle et schémas `javascript:`, `data:`, `vbscript:` et `file:` refusés ;
  - liens limités à `https://` ou à un chemin interne.
- **ETag** : empreinte du contenu modifiable. Elle couvre l'état, le titre, le corps, le résumé et les
  sources pour une leçon, la composition pour une version de module, et la composition et les prérequis
  pour une édition.
  - `PATCH`, `PUT` et `DELETE` sans `If-Match` répondent **428** `precondition-required`.
  - Un ETag périmé donne **412** `stale-draft`.
  - Le contrôle se fait sous verrou de ligne : de deux écritures concurrentes, une seule réussit.
- **Numérotation** : `max + 1` sous verrou consultatif par leçon × langue × juridiction, par module et
  par programme. Il n'y a pas de course entre deux créations.
- **Soumission d'une version de leçon** :
  - le corps est revalidé ;
  - une procédure (`steps`) ou un encadré de sécurité impose au moins une source citée ;
  - chaque bloc `reference` doit renvoyer à une source rattachée.
- **Approbation d'une version de leçon** :
  - `next_review_due` doit être future, à 3 ans au plus ;
  - toutes les sources citées doivent être **actives et vérifiées**.
- **Publication** (`queries.ensure_publishable`), en plus des déclencheurs :
  - l'approbation de l'édition est journalisée par le relecteur enregistré, sur l'empreinte courante ;
  - chaque version de module et de leçon est approuvée, avec une décision `approved` du relecteur
    enregistré ;
  - aucune échéance de révision n'est dépassée ;
  - pour un programme médical, chaque leçon a une source active et vérifiée, et aucune source citée
    n'est retirée.

  Toute anomalie répond **422** `publication_requirements` avec la liste des anomalies : identifiants et
  types, jamais de contenu.

## 4. Codes d'erreur (`application/problem+json`, champ `code`)

| HTTP | `type` | Codes |
|---|---|---|
| 401 | `unauthorized` | session absente, invalide ou expirée |
| 403 | `reauthentication-required` | session non sensible (> 30 min) |
| 403 | `forbidden` | `forbidden`, `actor_missing`, `reviewer_required` |
| 403 | `independence-required` | `self_approval`, `reviewer_not_independent` |
| 403 | `medical-publication-disabled` | `medical_publication_disabled` |
| 404 | `not-found` | ressource inconnue, **hors portée**, identifiant malformé, `kind` inconnu |
| 409 | `editorial-state-conflict` | `transition_forbidden`, `not_draft`, `immutable`, `in_use`, `history`, `legacy_structure`, `publish_via_release`, `born_draft`, `frozen_program` |
| 409 | `conflict` | `version_sequence`, `23505` (doublon), `40001`/`40P01` |
| 412 | `stale-draft` | ETag périmé |
| 422 | `invalid-input` | contrat Pydantic, `comment_required`, `review_date_required`, `invalid_content`, `forged_review`, `incomparable`, `duplicate_module`, `prerequisite_scope` |
| 422 | `publication-requirements` | `missing_source`, `source_unverified`, `unapproved_component`, `unsubmitted_component`, `tampered`, `incoherent_reference`, `empty_release`, `empty_module_version`, `empty_content`, `missing_base_locale`, `prerequisite_cycle`, `organization_scope`, `publication_requirements` |
| 422 | `invalid-reference` | `23503` |
| 428 | `precondition-required` | `If-Match` absent |
| 500 | `internal` | erreur non répertoriée : journalisée par SQLSTATE seulement, sans texte SQL |

**Traduction des erreurs.** Le `HINT 'nl:<code>'` de 0006 est lu en priorité. Viennent ensuite la
liste **fermée** de fragments des messages 0005 (`LEGACY_MESSAGES`), puis le SQLSTATE. Le texte SQL
n'est jamais renvoyé.

**Couverture.** `test_every_editorial_database_rule_has_a_stable_code` analyse les fichiers 0005 et
0006 et vérifie deux points :
- chaque `RAISE` éditorial sans indice a un code ;
- chaque indice `nl:` éditorial est connu.

## 5. Sécurité (Mission 4)

| # | Exigence | Mise en œuvre | Test |
|---|---|---|---|
| 1 | Identité issue de la session | `nl.actor_id` = `ctx.user_id` ; `author_id` posé par l'API ; contrats sans champ d'identité | `test_strict_contracts_refuse_state_and_identity_fields`, `test_workflow_independence_and_history` |
| 2 | Aucun rôle auto-attribuable | aucune route éditoriale ne touche aux rôles ; garde 0004 inchangée | `test_strict_contracts…`, suite identité |
| 3 | Permissions à chaque opération | `ensure_can` / `ensure_can_create` + `nl_user_has_permission` en base | `test_rbac_matrix_for_creation`, `test_publication_requires_publisher_in_scope` |
| 4 | 404 hors établissement | `load()` → `ensure_visible` ; listes filtrées par portée | `test_establishment_isolation`, `test_module_version_and_release_contracts` |
| 5 | 403 action interdite visible | `ensure_can`, membre sans permission → 403 | `test_rbac_matrix_for_creation`, `test_workflow_independence_and_history` |
| 6 | CSRF | API **Bearer seulement** : un cookie n'authentifie pas ; cookies limités au serveur Next.js | `test_authentication_required` |
| 7 | Concurrence | `FOR UPDATE` + ETag, verrous consultatifs de numérotation | `test_concurrent_patches_with_same_etag_one_wins`, `test_concurrent_creations_get_distinct_numbers` |
| 8 | Aucune fuite SQL | `translate()` ; détail générique ; `problem_code()` rejette tout mot SQL | `test_database_errors_are_stable_problems_without_sql` |
| 9 | Aucun contenu médical non validé publié | drapeau désactivé ; `ensure_publishable` ; déclencheurs 0006 ; nature médicale figée | `test_medical_publication_is_disabled`, `test_publication_checks_refuse_withdrawn_sources` |
| 10 | Aucun contournement | aucune route de publication directe ; pas de paramètre de contournement ; `is_medical` figé après soumission | `test_route_inventory_and_no_direct_publication`, `test_medical_publication_is_disabled` |
| 11 | Audit sans secret ni contenu | `record()` refuse `body`, `title`, `comment`, `reason`, `text`… ; seul la longueur du motif de retrait est notée | `test_audit_trail_without_content` |
| 12 | Tentatives d'élévation | auto-approbation (même avec `content:review`), relecteur non indépendant (D12), vérification de source par un formateur ou un relecteur d'établissement, publication par l'auteur | `test_workflow_independence_and_history`, `test_reviewer_must_be_independent_of_included_versions`, `test_sources_registration_verification_and_withdrawal` |

## 6. Tests

Environnement réel : PostgreSQL 16.15 + pgvector 0.8.0, Redis 7.0.15, migrations 0001 → 0006 et données
de référence. Les comptes sont **exclusivement des comptes de test** (`@example.test`) ; leurs rôles
sont posés par la garde de test `nl.role_grant_bypass`. Aucun relecteur réel n'est simulé et les
contenus sont fictifs. Les tests éditoriaux utilisent le métier `INTL/care_assistant_generic`, pour ne
pas toucher aux compteurs du pilote PAB. Un seul programme **non médical** est publié puis archivé
dans le test. Aucun contenu médical n'est publié.

| Suite | Résultat |
|---|---|
| `services/api/tests/api/test_editorial.py` | **29 réussis** |
| `services/api/tests/api tests/unit` (API complète + unitaires) | **116 réussis, 1 ignoré** (Mailpit, hors Docker), exécutée deux fois de suite sur la même base : stable |
| `database/tests` (migrations 0001→0006, règles, retour arrière) | **121 réussis** |
| Ruff `check` + `format --check` (dépôt) | OK, 103 fichiers |
| mypy (`app`, strict) | OK, 44 fichiers |
| pip-audit (dépendances de l'API) | aucune vulnérabilité, à jour (`opentelemetry-api` 1.45.2, celle qu'installe la CI) |

Les 29 tests éditoriaux couvrent :
- l'inventaire des routes ;
- l'authentification ;
- les contrats ;
- les 404 et les `kind` inconnus ;
- la matrice RBAC ;
- l'isolation des établissements ;
- la session sensible ;
- l'ETag et la numérotation ;
- deux scénarios de concurrence ;
- l'immuabilité ;
- les contrats des versions de module et des éditions ;
- le diff ;
- le workflow et l'historique ;
- l'indépendance D12 ;
- les composants non soumis ;
- l'abandon et la suppression ;
- la file de relecture ;
- le relecteur d'établissement ;
- les sources (enregistrement, vérification, retrait) ;
- les sources exigées ;
- la publication non médicale puis l'archivage ;
- la publication médicale désactivée ;
- les contrôles de publication ;
- la portée de l'éditeur ;
- l'audit sans contenu ;
- les erreurs PostgreSQL ;
- la traduction et sa couverture.

Ajustements hors du domaine éditorial :
- `tests/api/test_api.py` : `/api/v1/editorial/` est ajouté aux préfixes autorisés en écriture. Le
  catalogue et les référentiels restent en lecture seule.
- `app/domains/identity/router.py` : annotation de type `user_id: uuid.UUID`, exigée par mypy 2.3 et
  SQLAlchemy 2.1. Le comportement ne change pas.

Validation Windows : `scripts\validate-phase2.2.2b.ps1`. Il enchaîne `validate-phase2.2.2a.ps1`, et
donc toute la non-régression, puis effectue des contrôles en lecture seule sur l'API et la base de
développement.

## 7. Risques et limites

| Risque ou limite | Niveau | Traitement |
|---|---|---|
| Le catalogue public (`/api/v1/programs/{id}`) lit encore `program_modules` et « une » version publiée par leçon, pas l'édition publiée | Moyen | **2.2.2-c**. Aucune donnée réelle n'est concernée : aucun contenu n'est publié en production |
| Après archivage d'une édition, ses versions de leçon et de module restent `published` (historique 0006) | Faible | Le catalogue 2.2.2-c lira l'édition publiée, et non l'état des versions |
| MFA des relecteurs non encore exigée (D9) | Élevé avant production | Publication médicale réelle désactivée par configuration |
| Relecteur qualifié réel absent (D10) | Bloquant pour la mise en ligne | Aucun relecteur fictif ; le pilote PAB reste en brouillon |
| Sources : vérification datée du jour, sans pièce justificative | Moyen | La traçabilité (`verified_by`, date, audit) suffit pour 2.2.2-b ; une pièce jointe est prévue au lot médias |
| Interface éditoriale (Next.js) absente | — | Hors périmètre 2.2.2-b ; l'API est prête |
| La file de relecture et les listes sont limitées à 500 éléments, sans pagination | Faible | Pagination à ajouter avec l'interface |

## 8. État Git

- Branche locale : `phase-2.2.2b-editorial-api`, sur `000a1f3`.
- Un commit local, **non poussé**, comme demandé.
- Aucune migration historique n'a été modifiée et aucune donnée n'a été supprimée.
- Aucun service payant n'est utilisé et rien n'a été développé pour 2.2.2-c.

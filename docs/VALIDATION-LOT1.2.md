# Rapport de validation — Lot 1.2 (socle exécutable)

> 18 novembre 2026 · branche `lot-1.2` (non poussée) · statut : **développé, validation réelle en attente**

## 1. Périmètre (approuvé le 18/11/2026)

| Inclus | Livré |
|---|---|
| API FastAPI : configuration, erreurs RFC 9457, journaux JSON sans données personnelles, `/health`, `/ready`, OpenAPI | ✅ `services/api/app` |
| Lecture seule : juridictions, métiers par juridiction, spécialités, programmes, modules (publiés uniquement) | ✅ |
| Modèles SQLAlchemy du périmètre + test de cohérence modèle ↔ schéma | ✅ 9 tables (les 51 autres en Phase 2, voir §5) |
| Dockerfile API + service `api` (profil `app`) | ✅ |
| Web Next.js : accueil (mention « pas un diplôme »), choix du pays, choix du métier, catalogue (état « contenu en préparation »), FR/EN, thèmes | ✅ `apps/web` |
| Structure du module pilote « Hygiène des mains et prévention des infections » (PAB Québec), **DRAFT, sans contenu médical** | ✅ `database/seeds/0002_pilot_pab_structure.sql` |
| Tests : API, e2e Playwright, accessibilité axe ; jobs CI | ✅ écrits · ⏳ exécution réelle (§3) |

Hors périmètre, conformément à votre décision : authentification, lecteur de leçons, quiz, progression, tableau de bord (Phase 2, non autorisée). Aucun contenu médical publié.

## 2. Garanties de sécurité pédagogique dans le lot

- Le catalogue n'expose que `status = 'published'` **et** `organization_id IS NULL` ; une leçon n'apparaît que si elle a une version publiée, laquelle exige (base de données) un relecteur médical habilité distinct de l'auteur.
- Le parcours pilote est créé **en brouillon** : programme, module, 4 leçons et 8 versions FR/EN aux titres de travail « (à rédiger) », sans bloc de contenu. Tests : `test_pilot_contains_no_published_or_reviewed_content`, `test_pilot_draft_is_never_exposed`, e2e « le module pilote n'apparaît jamais ».
- L'interface affiche la mention de non-équivalence à un diplôme, la non-équivalence des titres entre pays et le badge « informations en cours de validation » pour les métiers non validés.
- L'API n'expose que des routes GET (test `test_openapi_documents_lot_1_2_routes`).

## 3. Validations

### Exécutées dans l'espace de travail
| Contrôle | Résultat |
|---|---|
| Tests base de données + migrations (PostgreSQL 16, sans pgvector) | ✅ 48 réussis, 1 ignoré (pgvector) — dont 3 nouveaux tests du parcours pilote |
| Données 0002 appliquées deux fois | ✅ 1 programme, 1 module, 4 leçons, 8 versions, tout en `draft` |
| Compilation Python de l'API, YAML du workflow et du compose | ✅ |

### NON exécutées à ce stade (impossible dans l'espace de travail : aucun paquet Python/Node installable)
- Tests de l'API (FastAPI, SQLAlchemy, asyncpg) ;
- `next build`, vérification TypeScript, parcours Playwright, audit axe ;
- construction des images Docker `api` et `web`.

Elles seront prouvées par **l'une ou l'autre** de ces voies :
1. `scripts/validate-lot1.2.ps1` sur votre PC (Docker) — 22 contrôles, dont pytest API et Playwright + axe ;
2. la CI GitHub (jobs `api`, `web`, `compose`) après push de la branche `lot-1.2` (**autorisation requise**).

<!-- RESULTATS -->

## 4. Critères de validation du lot 1.2

| # | Critère | État |
|---|---|---|
| L1 | Images `api` et `web` construites ; services sains | ⏳ |
| L2 | Tests d'API réussis sur PostgreSQL + Redis réels | ⏳ |
| L3 | Parcours e2e accueil → pays → métier → catalogue réussi en FR et EN | ⏳ |
| L4 | Aucune violation axe « serious »/« critical » sur les 4 écrans | ⏳ |
| L5 | Le module pilote n'est visible nulle part (API et web) | ⏳ (prouvé côté base : ✅) |
| L6 | CI GitHub verte sur `lot-1.2` | ⏳ |

## 5. Écarts assumés et points ouverts

- **Modèles SQLAlchemy** : seules les 9 tables utilisées par le lot sont modélisées ; le test compare chaque colonne modélisée au schéma réel. La modélisation complète et `alembic check` sont reportés à la Phase 2 (lot 2.2), où les écritures commencent.
- **`package-lock.json`** absent (aucun accès au registre npm ici) : il sera créé au premier `npm install` (par le script ou la CI) et devra être validé dans Git pour figer les versions.
- **Politique CSP** non encore définie pour le web (en-têtes de base présents) : Phase 7.
- **Linters** (Ruff, ESLint) non encore branchés en CI.

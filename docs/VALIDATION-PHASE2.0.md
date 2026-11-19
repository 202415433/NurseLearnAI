# Rapport de validation — Phase 2.0 (sécurité et qualité)

> 18 novembre 2026 · branche `phase-2.0-security-quality` (locale, **non poussée**) · statut : **validée sur Windows — 18/18 contrôles Phase 2.0 et 52/52 de non-régression (exécution n° 5)**

À lire avec [`SECURITY-AUDIT-PHASE2.0.md`](SECURITY-AUDIT-PHASE2.0.md) (analyse détaillée) et [`PHASE2.1-PLAN.md`](PHASE2.1-PLAN.md) (suite proposée).

## 1. Préalables

| Étape | Résultat |
|---|---|
| PR #3 (documentation seule, CI 6/6, sans conflit) | Fusionnée par commit de fusion `1134cd5` ; CI de `main` verte (run 44427267562) |
| Branche | `phase-2.0-security-quality` créée depuis `1134cd5` |

## 2. Commandes réellement exécutées

```powershell
# Audit initial (lockfile de la Phase 1.2) — votre poste, 20 h 18
docker run --rm -v "${PWD}:/repo" -w /repo/apps/web node:22-bookworm sh -c "npm audit --package-lock-only --json > /repo/docs/validation-logs/npm-audit.json; npm audit --package-lock-only --omit=dev --json > /repo/docs/validation-logs/npm-audit-prod.json"

# Validation complète — votre poste (exécutions n° 1 à 5)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.0.ps1
```

`validate-phase2.0.ps1` enchaîne :
1. `scripts/npm-lock.sh` (lockfile sur une copie, vérification de postcss) ;
2. `npm audit --package-lock-only` (complet et production) et `pip-audit --strict` ;
3. `ruff check`, `ruff format --check`, `mypy` ; `eslint`, `prettier --check`, `tsc --noEmit` ;
4. `validate-phase1.2.ps1` (non-régression : `docker compose up --build -d`, API, Web, pytest, Vitest, Playwright + axe) ;
5. les contrôles HTTP de la Phase 2.0.

Dans l'espace de travail de l'assistant (sans accès au registre npm) : `ruff check`, `ruff format`, `mypy --ignore-missing-imports`, `prettier --check`, tests PostgreSQL et unitaires sans FastAPI (`database/tests`, `tests/unit/test_clientip.py`).

## 3. Exécutions et corrections

| N° | Heure | Résultat | Cause | Correction |
|---|---|---|---|---|
| 1 | 20 h 18 | 13 échecs | `validate-phase1.2.ps1` avait perdu son BOM UTF-8 : Windows PowerShell 5.1 ne pouvait plus l'exécuter, et la non-régression relisait l'ancien journal (faux 52/52) ; anomalie npm « edgesOut » ; `pip-audit --skip-editable` refusé en mode strict ; 1 erreur mypy | BOM rétabli sur tous les scripts et contrôlé en CI ; journal de l'exécution exigé ; pip-audit sur `pip freeze` ; annotation mypy |
| 2 | 20 h 33 | 13 échecs | Le repli « lockfile reconstruit » **supprimait `package-lock.json` avant de le régénérer** : le fichier a disparu quand la régénération a échoué | Fichier restauré depuis Git ; nouveau `scripts/npm-lock.sh` qui travaille sur une copie et arrête la validation tôt en cas d'échec |
| 3 | 21 h 14 | Arrêt au lockfile | Diagnostic par variantes : seule « vitest 3 sans override » passe → l'anomalie vient de la **mise à niveau vitest 3 → 4 avec npm 10**, pas de l'override | Tentatives npm 10 puis **npm 11** ; `npm ci` partout (installations reproductibles) |
| 4 | 21 h 26 | 3 échecs (15/18) | Lockfile généré par npm 11 ; critiques 2 → 0 ; mais (a) npm n'applique pas un override à une entrée déjà verrouillée : postcss 8.4.31 conservé ; (b) Vitest 4 utilise Vite 8 (Oxc), l'option `esbuild.jsx` ne s'appliquait plus ; (c) 1 erreur et 1 avertissement ESLint | Entrée postcss retirée avant résolution et **vérification que toutes les copies sont ≥ 8.5.23** ; `oxc.jsx.runtime = "automatic"` ; ESLint corrigé |
| **5** | **21 h 41** | **18/18 + 52/52** | — | — |

## 4. Résultats de l'exécution n° 5 (commit `051477d`, lockfile final intégré ensuite)

Journaux : `docs/validation-logs/phase2.0-20261118-214106.log` et `phase1.2-20261118-214844.log`.

| Domaine | Résultat |
|---|---|
| Lockfile | `postcss : toutes les copies ≥ 8.5.23` ; `lock-ok (npm 10, lockfile existant)` |
| **npm audit** | **0 vulnérabilité** (critique 0, élevée 0, modérée 0, faible 0) sur 515 dépendances |
| pip-audit | Aucune vulnérabilité connue |
| Python | Ruff : aucune anomalie ; 55 fichiers formatés ; mypy : 0 erreur (19 fichiers) |
| Web | ESLint : 0 problème ; Prettier : conforme ; TypeScript : 0 erreur |
| Pile Docker | `docker compose up --build -d` : 6 services sains (PostgreSQL, Redis, RustFS, Mailpit, API, Web) |
| PostgreSQL et migrations | Persistance après redémarrage (12 métiers avant et après) ; migrations à `0003_business_rules` ; aucune migration historique modifiée |
| **pytest** | **104 réussis** (base, migrations, API, unitaires ; 92 en Phase 1.2) ; **plus d'avertissement httpx** |
| **Vitest** | **21/21** (dont 3 nouveaux tests CSP) |
| **Playwright + axe** | **10/10** (dont 3 nouveaux scénarios de sécurité) ; le scénario « programme publié » s'exécute en CI uniquement |
| Sécurité HTTP API | `X-Frame-Options`, CSP, sans en-tête `Server`, en-têtes `RateLimit-*`, `X-Forwarded-For` d'un client non approuvé ignoré (299 → 298 → 297), 404 problem+json |
| Sécurité HTTP Web | CSP avec nonce unique, `frame-ancestors 'none'`, sans `unsafe-eval` ; `X-Frame-Options: DENY` ; sans `X-Powered-By` ; **404 réels** sur 3 adresses |
| Non-régression Phase 1.2 | **52/52** |

## 5. Changements apportés

| Domaine | Changement |
|---|---|
| Dépendances web | `vitest` 3.2.7 → **4.1.12** (Vite 8) ; `postcss` de Next.js 8.4.31 → **8.5.33** (override limité à `next`) ; `next` ≥ 15.5.29 ; ajout d'`eslint` 9, `eslint-config-next`, `@eslint/eslintrc`, `prettier` |
| Dépendances API | `httpx` → **`httpx2`** (client de test recommandé par Starlette) ; ajout de `ruff`, `mypy` (développement) |
| Qualité | `ruff.toml` (E, F, W, I, B, UP, S, ASYNC, SIM ; migrations exclues) ; mypy (`app/`, plugin Pydantic) ; `eslint.config.mjs` (Next.js core-web-vitals + TypeScript) ; Prettier ; code reformaté |
| CI | Ruff, mypy, ESLint, Prettier, `npm ci` ; avertissement httpx traité comme une erreur ; contrôle du BOM des scripts ; **7ᵉ job `dependency-audit`** (`npm audit --audit-level=high`, `pip-audit --strict`) |
| Sécurité HTTP | Voir [`SECURITY-AUDIT-PHASE2.0.md`](SECURITY-AUDIT-PHASE2.0.md) §3 |
| Scripts | `validate-phase2.0.ps1`, `npm-lock.sh`, `diagnose-ports.ps1` ; BOM sur tous les scripts PowerShell |

## 6. Modèles de données : écarts documentés

Le schéma compte **60 tables** ; l'API en lecture n'en modélise que **10** (`countries`, `locales`, `jurisdictions`, `professions`, `specialties`, `programs`, `modules`, `program_modules`, `lessons`, `lesson_versions`), chacune vérifiée colonne par colonne contre la base (`test_models_match_schema`).

Les 50 autres ne sont pas utilisées par le code actuel ; elles seront modélisées **au moment où un lot les utilise**, sans toucher au schéma existant :

| Lot | Tables à modéliser |
|---|---|
| 2.1 Identité | `users`, `profiles`, `roles`, `permissions`, `role_permissions`, `user_roles`, `auth_sessions`, `audit_logs`, `organizations` |
| 2.2 Progression et quiz | `quizzes`, `quiz_questions`, `questions`, `answers`, `question_references`, `exams`, `exam_questions`, `exam_attempts`, `attempt_responses`, `student_progress`, `xp_events`, `badges`, `user_badges`, `review_cards`, `study_plans`, `study_plan_items` |
| 2.3 Auteur et relecture | `learning_objectives`, `objective_competencies`, `competencies`, `competency_scopes`, `competency_assessments`, `medical_references`, `lesson_version_references`, `learning_resources`, `module_prerequisites`, `content_issue_reports` |
| 2.4 Formateurs et établissements | `instructors`, `cohorts`, `cohort_members`, `certificates` |
| Plus tard (hors Phase 2) | `ai_conversations`, `ai_messages`, `ai_knowledge_sources`, `ai_knowledge_chunks`, `ai_prompt_templates` (tuteur IA), `clinical_simulations`, `simulation_attempts`, `simulation_competencies`, `plans`, `subscriptions`, `payments` |

## 7. Risques restants

| Risque | Traitement |
|---|---|
| Next.js 15 : support de sécurité jusqu'au 31/01/2027 | Migration vers Next.js 16 à planifier avant cette date |
| Override `postcss` | À retirer quand une version 15.5.x de Next.js embarquera un postcss corrigé |
| npm 10 ne sait pas résoudre la mise à niveau vitest 4 (le lockfile a été produit par npm 11) | `npm ci` (npm 10) l'accepte ; pour les futures mises à jour, utiliser `scripts/npm-lock.sh` (repli npm 11) |
| `pip-audit --no-deps` sans empreintes (hashes) | Acceptable ; un lockfile Python avec empreintes est proposé au lot 2.5 |
| `style-src 'unsafe-inline'`, limite de débit contournable si le Web est exposé sans reverse proxy, HSTS | Voir [`SECURITY-AUDIT-PHASE2.0.md`](SECURITY-AUDIT-PHASE2.0.md) §3 |
| CI GitHub non exécutée sur cette branche | Exige votre autorisation de push (7 jobs attendus) |

## 8. Règles respectées

Aucun push ni aucune fusion de la Phase 2.0, aucune réécriture d'historique, aucune donnée ni aucun volume supprimés, aucune migration historique modifiée, aucun contenu médical publié, lot 2.1 non commencé.

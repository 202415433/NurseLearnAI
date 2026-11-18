# 07 — Préparation de la Phase 2 (MVP fonctionnel)

> **Statut : prêt à démarrer après validation de la Phase 1.** Aucun code de Phase 2 n'a été écrit.

## 1. Objectifs

Livrer une application web utilisable de bout en bout pour un parcours pilote : un apprenant s'inscrit, choisit sa juridiction et son métier, suit un module, répond aux quiz et voit sa progression ; un formateur crée du contenu ; un relecteur médical le valide ; un administrateur gère les comptes et le catalogue.

## 2. Lots de travail

| Lot | Contenu | Estimation |
|---|---|---|
| 2.1 Socle API | Configuration (pydantic-settings), session async, gestion d'erreurs RFC 9457, journalisation JSON, `/health`, OpenAPI, Dockerfile, service `api` dans docker-compose (profil `app`) | 1 sem. |
| 2.2 Modèles SQLAlchemy | Modèles des 60 tables alignés sur le schéma ; test `alembic check` sans divergence | 1 sem. |
| 2.3 Identité | Inscription, vérification e-mail, connexion, rafraîchissement rotatif, réinitialisation, RBAC, profils, export des données | 2 sem. |
| 2.4 Référentiels & catalogue | Juridictions, métiers, spécialités ; programmes/modules/leçons en lecture avec résolution langue + juridiction ; cache Redis | 1,5 sem. |
| 2.5 Auteur & relecture | Éditeur de blocs, versions, soumission, relecture à quatre yeux, publication, références, signalements | 2 sem. |
| 2.6 Quiz & progression | 4 formats (choix unique, vrai/faux, choix multiples, réponse courte simple), tentatives, correction, progression, compétences niveau théorique | 1,5 sem. |
| 2.7 Web | Next.js : écrans 1–7, 11–14, 16 (minimal) ; i18n FR/EN ; thèmes ; client API généré | 4 sem. (en parallèle) |
| 2.8 Qualité | Tests e2e Playwright, audit axe, couverture, guide de démarrage Windows | continu |

## 3. Points d'API prévus (`/api/v1`)

| Méthode | Chemin | Rôle requis |
|---|---|---|
| POST | `/auth/register`, `/auth/verify-email`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/password/forgot`, `/auth/password/reset` | public / authentifié |
| GET, PATCH | `/me`, `/me/profile` | authentifié |
| GET | `/me/export` | authentifié |
| GET | `/jurisdictions`, `/jurisdictions/{code}/professions`, `/specialties` | public |
| GET | `/programs`, `/programs/{id}`, `/modules/{id}`, `/lessons/{id}` | public (gratuit) / abonné |
| POST | `/lessons/{id}/progress` | `learning:participate` |
| POST | `/quizzes/{id}/attempts` ; PUT `/attempts/{id}/responses/{question_id}` ; POST `/attempts/{id}/submit` | `learning:participate` |
| GET | `/me/dashboard` | authentifié |
| POST, PATCH | `/authoring/modules`, `/authoring/lessons/{id}/versions` | `content:create` |
| POST | `/authoring/lesson-versions/{id}/submit` ; `/review/lesson-versions/{id}/approve` ; `/reject` ; `/publish` | `content:create` / `content:review` / `content:publish` |
| POST | `/issues` ; GET, PATCH `/admin/issues` | authentifié / `content:review` |
| GET, PATCH | `/admin/users`, `/admin/users/{id}/roles` | `user:manage` |
| GET | `/admin/audit-logs` | `audit:read` |

## 4. Contenu de démonstration

Module pilote **« Hygiène des mains et prévention des infections »** (niveau Débutant, groupe A), en FR et EN, rédigé à partir de sources publiques identifiables (OMS, ASPC, INSPQ), 4 leçons + 1 quiz de 10 questions + fiche de révision. Il restera au statut `in_review` tant qu'un professionnel qualifié ne l'a pas relu (la base empêche toute publication sans relecteur).

## 5. Définition de « terminé » (chaque fonctionnalité)

1. Code relu ; 2. tests unitaires et d'intégration verts en CI ; 3. route documentée dans l'OpenAPI ; 4. action sensible journalisée dans `audit_logs` ; 5. chaînes traduites FR/EN ; 6. écran vérifié au clavier et par axe ; 7. limitations signalées dans le rapport de phase.

## 6. Dépendances à installer (le poste de développement doit avoir accès au réseau)

Python : `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2`, `asyncpg`, `psycopg[binary]`, `alembic`, `pydantic-settings`, `argon2-cffi`, `pyjwt`, `redis`, `arq`, `httpx`, `structlog` ; tests : `pytest`, `pytest-asyncio`, `testcontainers`. JavaScript : `next`, `react`, `typescript`, `tailwindcss`, `next-intl`, `openapi-typescript`, `@playwright/test`, `@axe-core/playwright`.

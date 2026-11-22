# NurseLearn AI

Plateforme d'apprentissage des soins infirmiers et de l'assistance aux soins — web, Android, iOS — multilingue (FR/EN) et adaptée à plusieurs juridictions (Québec, autres provinces canadiennes, France, Sénégal, pays francophones, référentiel international).

> **NurseLearn AI forme et prépare. Ses attestations internes ne sont ni un diplôme, ni une licence, ni une autorisation d'exercer.**

## État du projet

| Phase | Statut |
|---|---|
| 1 — Analyse et architecture | ✅ livrée — [`docs/VALIDATION-PHASE1.md`](docs/VALIDATION-PHASE1.md) |
| 1.1 — Validation technique | ✅ 30/30 sur Windows, CI 4/4, intégrée dans `main` (#1) — [`docs/VALIDATION-PHASE1.1.md`](docs/VALIDATION-PHASE1.1.md), décision : [`docs/PHASE2-READINESS.md`](docs/PHASE2-READINESS.md) |
| 1.2 — API publique et parcours web | ✅ 52/52 sur Windows, CI 6/6, intégrée dans `main` (#2) — [`docs/VALIDATION-PHASE1.2.md`](docs/VALIDATION-PHASE1.2.md) · [API](docs/API-REFERENCE.md) · [interface](docs/FRONTEND-ARCHITECTURE.md) |
| 2.0 — Sécurité et qualité | ✅ intégrée dans `main` (#4) — [`docs/VALIDATION-PHASE2.0.md`](docs/VALIDATION-PHASE2.0.md) · [audit](docs/SECURITY-AUDIT-PHASE2.0.md) |
| 2.1 — Identité et accès | ✅ validée (42/42 + 52/52), CI 7/7, PR #6 — [`docs/VALIDATION-PHASE2.1.md`](docs/VALIDATION-PHASE2.1.md) · [sécurité](docs/SECURITY-IDENTITY-PHASE2.1.md) · [préparation 2.2](docs/PHASE2.2-READINESS.md) |
| 2.2.1 — Modèle pédagogique et règles éditoriales | ✅ intégré dans `main` (#8), CI 7/7 — [`docs/VALIDATION-PHASE2.2.1.md`](docs/VALIDATION-PHASE2.2.1.md) · [ADR-0009](docs/adr/0009-editions-figees-et-workflow-editorial.md) · [préparation 2.2.2](docs/PHASE2.2.2-READINESS.md) |
| 2.2.2-a — Versions de modules indépendantes (migration 0006, ADR-0010) | ✅ intégrée dans `main` (#10), CI 7/7 — [`docs/VALIDATION-PHASE2.2.2A.md`](docs/VALIDATION-PHASE2.2.2A.md) · [préparation 2.2.2-b](docs/PHASE2.2.2B-READINESS.md) |
| 2.2.2-b — API éditoriale (`/api/v1/editorial`, 37 opérations) | ✅ intégrée dans `main` (#12), CI 7/7 — [`docs/VALIDATION-PHASE2.2.2B.md`](docs/VALIDATION-PHASE2.2.2B.md) |
| 2.2.2-c — Catalogue lu depuis l'édition publiée | ✅ intégrée dans `main` (#13), CI 7/7 — [`docs/VALIDATION-PHASE2.2.2C.md`](docs/VALIDATION-PHASE2.2.2C.md) |
| 2.2.3 — Médias et documents protégés | ✅ 2.2.3-a (migration 0007, invariants, quotas) — [`docs/VALIDATION-PHASE2.2.3A.md`](docs/VALIDATION-PHASE2.2.3A.md) · 🧪 2.2.3-b (téléversement sécurisé, quarantaine privée) validée localement, en attente d'autorisation — [`docs/VALIDATION-PHASE2.2.3B.md`](docs/VALIDATION-PHASE2.2.3B.md) · [`docs/PHASE2.2.3C-READINESS.md`](docs/PHASE2.2.3C-READINESS.md) · [`docs/PHASE2.2.3-PLAN.md`](docs/PHASE2.2.3-PLAN.md) · [ADR-0011](docs/adr/0011-medias-antivirus.md) |
| 2.2.3 à 2.5 | 🗓️ planifiées — [`docs/07-plan-phase-2.md`](docs/07-plan-phase-2.md) |
| 3 à 7 | 🗓️ planifiées — voir [`docs/06-roadmap.md`](docs/06-roadmap.md) |

## Documentation

1. [Analyse des besoins](docs/01-analyse-besoins.md)
2. [Spécifications fonctionnelles](docs/02-specifications-fonctionnelles.md)
3. [Architecture technique](docs/03-architecture-technique.md)
4. [Modèle de données](docs/04-modele-donnees.md)
5. [Design system](docs/05-design-system.md)
6. [Roadmap](docs/06-roadmap.md)
7. [Plan de la Phase 2](docs/07-plan-phase-2.md)
8. [Décisions d'architecture (ADR)](docs/adr/)
9. [Référence de l'API](docs/API-REFERENCE.md)
10. [Architecture de l'interface web](docs/FRONTEND-ARCHITECTURE.md)
11. [Plan du lot 2.2 — moteur pédagogique](docs/PHASE2.2-PLAN.md) (proposé, non commencé)

## Structure

```
apps/web            Next.js (Phase 1.2)          apps/mobile        Expo (Phase 5)
services/api        FastAPI + Alembic (Phase 1.2) packages/design-tokens  jetons de design
database            schéma SQL, données de référence, tests de contraintes
compose.yaml        point d'entrée « docker compose up --build -d »
infra               docker-compose (PostgreSQL+pgvector, Redis, RustFS (S3), Mailpit, API, Web)
scripts             utilitaires PowerShell et Bash
```

## Démarrage rapide (Windows)

Prérequis : [Docker Desktop](https://www.docker.com/products/docker-desktop/), Git, Node.js 22, Python 3.12 (+ `pip install pytest`), client `psql` dans le PATH (installé avec PostgreSQL ou via `winget install PostgreSQL.PostgreSQL.16`).

```powershell
cd C:\NurseLearnAI-git               # seul dossier de travail (l'ancien C:\NurseLearnAI n'est plus utilisé)
Copy-Item .env.example .env          # puis remplacer toutes les valeurs "change-me"
docker compose -f infra/docker-compose.yml --env-file .env up -d
.\scripts\db-apply.ps1               # crée le schéma + données de référence
.\scripts\test-db.ps1                # tests du schéma et des migrations
cd packages\design-tokens; npm test  # contrastes WCAG du design system
```

Validation complète en une commande (Docker, Alembic réel, pgvector, tests, secrets) — aucune donnée existante supprimée :

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase1.1.ps1
```

Application complète (Phase 1.2) — PostgreSQL, Redis, RustFS, Mailpit, API et Web :

```powershell
docker compose up --build -d          # Docker Compose 2.20+ ; volumes de données conservés
# Web : http://localhost:3000/fr   ·   API : http://localhost:28000/docs
docker compose ps                     # état et santé des 6 services
docker compose stop                   # arrêt sans perte de données (ne jamais utiliser « down -v »)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase1.2.ps1   # validation complète (52 contrôles)
```

Interfaces locales : console S3 (RustFS) http://localhost:9001/rustfs/console/ · Mailpit http://localhost:8025

### Comptes et accès (Phase 2.1)

```powershell
docker compose up --build -d
# 1. Créer un compte étudiant :          http://localhost:3000/fr/inscription
# 2. Lire le courriel de vérification :  http://localhost:8025 (Mailpit, aucun envoi réel)
# 3. Se connecter, gérer son compte :    http://localhost:3000/fr/compte
# Exploitation (rôles hors inscription publique, journalisés) :
docker compose exec api python -m app.cli create-organization mon-ecole "Mon école" --kind school
docker compose exec api python -m app.cli grant-platform-admin personne@example.org   # compte déjà vérifié
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.1.ps1          # validation complète Phase 2.1
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.1.ps1        # sous-lot 2.2.1 (inclut 2.1, 2.0, 1.2)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2a.ps1       # étape 2.2.2-a (inclut 2.2.1 et antérieures)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2b.ps1       # étape 2.2.2-b (inclut 2.2.2-a et antérieures)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2c.ps1       # étape 2.2.2-c (inclut 2.2.2-b et antérieures)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3a.ps1       # étape 2.2.3-a (inclut 2.2.2-c et antérieures)
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3b.ps1       # étape 2.2.3-b (inclut 2.2.3-a et antérieures)
```

Seul le rôle étudiant s'obtient par inscription. Les rôles formateur, relecteur médical et administrateur d'établissement s'obtiennent uniquement par invitation (page « Mon compte » → « Gérer les invitations ») ; un administrateur d'établissement ne peut inviter que des formateurs dans son établissement. En production, `TOKEN_HASH_KEY` (≥ 32 caractères) et `SESSION_COOKIE_SECURE=true` sont obligatoires.

### Tests

```powershell
.\scripts\test-db.ps1                                     # schéma, contraintes, migrations (PostgreSQL)
cd services\api; python -m pytest -q tests                 # API (PostgreSQL + Redis ; Mailpit si MAILPIT_URL)
cd apps\web; npm test; npm run test:e2e                    # Vitest ; Playwright + axe (MAILPIT_URL pour l'identité)
```

## Principes non négociables

- Aucune donnée réelle de patient dans les simulations par défaut.
- Aucun contenu publié sans relecture par un professionnel qualifié, distinct de l'auteur.
- Aucun titre de métier considéré comme équivalent d'une juridiction à l'autre.
- Le tuteur IA cite ses sources approuvées et ne donne pas de conseil clinique personnel.
- Aucun paiement réel sans configuration explicite (`PAYMENTS_ENABLED=false` par défaut).

Ces règles sont garanties par des contraintes de base de données et couvertes par des tests.

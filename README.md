# NurseLearn AI

Plateforme d'apprentissage des soins infirmiers et de l'assistance aux soins — web, Android, iOS — multilingue (FR/EN) et adaptée à plusieurs juridictions (Québec, autres provinces canadiennes, France, Sénégal, pays francophones, référentiel international).

> **NurseLearn AI forme et prépare. Ses attestations internes ne sont ni un diplôme, ni une licence, ni une autorisation d'exercer.**

## État du projet

| Phase | Statut |
|---|---|
| 1 — Analyse et architecture | ✅ livrée — [`docs/VALIDATION-PHASE1.md`](docs/VALIDATION-PHASE1.md) |
| 1.1 — Validation technique | ✅ infrastructure validée 30/30 sur Windows, en attente du push et de la CI — [`docs/VALIDATION-PHASE1.1.md`](docs/VALIDATION-PHASE1.1.md), décision : [`docs/PHASE2-READINESS.md`](docs/PHASE2-READINESS.md) |
| 2 — MVP fonctionnel | 📋 préparée — voir [`docs/07-plan-phase-2.md`](docs/07-plan-phase-2.md) |
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

## Structure

```
apps/web            Next.js (Phase 2)          apps/mobile        Expo (Phase 5)
services/api        FastAPI + Alembic          packages/design-tokens   jetons de design
database            schéma SQL, données de référence, tests de contraintes
infra               docker-compose (PostgreSQL+pgvector, Redis, RustFS (S3), Mailpit)
scripts             utilitaires PowerShell et Bash
```

## Démarrage rapide (Windows)

Prérequis : [Docker Desktop](https://www.docker.com/products/docker-desktop/), Git, Node.js 22, Python 3.12 (+ `pip install pytest`), client `psql` dans le PATH (installé avec PostgreSQL ou via `winget install PostgreSQL.PostgreSQL.16`).

```powershell
cd C:\NurseLearnAI
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

Interfaces locales : console S3 (RustFS) http://localhost:9001/rustfs/console/ · Mailpit http://localhost:8025

## Principes non négociables

- Aucune donnée réelle de patient dans les simulations par défaut.
- Aucun contenu publié sans relecture par un professionnel qualifié, distinct de l'auteur.
- Aucun titre de métier considéré comme équivalent d'une juridiction à l'autre.
- Le tuteur IA cite ses sources approuvées et ne donne pas de conseil clinique personnel.
- Aucun paiement réel sans configuration explicite (`PAYMENTS_ENABLED=false` par défaut).

Ces règles sont garanties par des contraintes de base de données et couvertes par des tests.

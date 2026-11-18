# Préparation de la Phase 2 — décision GO / NO-GO

> 18 novembre 2026 · à lire avec [`VALIDATION-PHASE1.1.md`](VALIDATION-PHASE1.1.md)

## Décision

<!-- DECISION -->
## **NO-GO pour la Phase 2 aujourd'hui** — en attente de la validation réelle de la Phase 1.2.

Mise à jour du 18 novembre 2026 (Phase 1.2 développée).

### Ce qui est prouvé

- Infrastructure Phase 1.1 : 30/30 sur votre poste Windows (deux exécutions) et CI GitHub 4/4 (`phase-1.1`).
- Phase 1.2 : API publique en lecture seule, parcours web complet, Compose « une commande », tests écrits (API, unitaires, composants, e2e, accessibilité). Côté base : 48 tests réussis ici, le pilote reste invisible.

### Ce qui manque pour dire GO

1. **Exécution réelle de la Phase 1.2** : tests FastAPI, Vitest, `next build`, Playwright/axe et images Docker n'ont pas pu tourner dans l'espace de travail (aucun paquet installable). Preuve attendue : `scripts/validate-phase1.2.ps1` sans FAIL **et/ou** CI GitHub verte sur `phase-1.2` (push à autoriser).
2. **Fusion** de `phase-1.1` puis `phase-1.2` vers `main` (votre autorisation).
3. Archivage de l'ancien dossier `C:\NurseLearnAI`.

## Critères de passage (tous requis)

| # | Critère | Preuve attendue | État |
|---|---|---|---|
| C1 | Services Docker démarrés et sains (PostgreSQL+pgvector, Redis, stockage S3 RustFS, Mailpit), volumes persistants, aucun secret dans les journaux | Journal `docs/validation-logs/phase1.1-*.log` sans FAIL | <!-- C1 -->✅ exécution n° 4 : 30/30 |
| C2 | `alembic upgrade head` → 60 tables, pgvector, HNSW, 0003 ; `upgrade head` idempotent ; `downgrade base` → base vide ; réapplication OK | Même journal + job CI « Alembic réel » | <!-- C2 -->✅ exécution n° 4 (sur votre poste) · ⏳ job CI |
| C3 | Suite pytest complète **avec** pgvector, 0 test ignoré | Même journal + job CI « Schéma » | <!-- C3 -->✅ 46 réussis, 0 ignoré, pgvector réel (exécution n° 4) |
| C4 | Branche `phase-1.1` poussée, CI GitHub verte (4 jobs) | Page Actions | ✅ run 44391779337 : 4/4 réussis |
| C5 | Phase 1.2 livrée et vérifiée | `validate-phase1.2.ps1` sans FAIL + CI verte sur `phase-1.2` | 🔎 développée (branche `phase-1.2`, non poussée), validation réelle en attente — [`VALIDATION-PHASE1.2.md`](VALIDATION-PHASE1.2.md) |
| C6 | Un seul dossier de travail local (`C:\NurseLearnAI-git`) | — | ✅ utilisé pour la Phase 1.2 · ⏳ archivage de l'ancien dossier (vous) |
| C7 | Fusion vers `main` des branches validées | Historique Git | ⏳ (autorisation) |

Non bloquant pour la Phase 2, mais bloquant pour toute **publication** : au moins un relecteur médical qualifié (Québec) inscrit avec le rôle `medical_reviewer`.

## Points à traiter au début de la Phase 2 (issus de la Phase 1.2)

| Sujet | Action prévue |
|---|---|
| Limite de débit par visiteur | Proxy d'entrée + `X-Forwarded-For` de confiance (`--forwarded-allow-ips`) ; aujourd'hui limite globale configurable |
| Modèles SQLAlchemy | Modélisation complète des 60 tables + `alembic check` en CI (lot 2.2) |
| CSP stricte | Nonce pour le script de thème, en-tête CSP dans Next.js |
| Linters | Ruff (API), ESLint (web) en CI |
| `package-lock.json` | Versionner après le premier `npm install` |
| Relecteur médical | Inscrire au moins un relecteur qualifié (rôle `medical_reviewer`) avant toute publication |

## Séquence recommandée

1. ~~Validation Phase 1.1 (30/30) et CI `phase-1.1` (4/4)~~ — fait.
2. Vous lancez `scripts/validate-phase1.2.ps1` sur `C:\NurseLearnAI-git` (branche `phase-1.2`).
3. Vous autorisez le push de `phase-1.2` ; je vérifie la CI GitHub (6 jobs).
4. Vous autorisez les fusions vers `main`.
5. Décision GO Phase 2.

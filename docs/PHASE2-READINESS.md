# Préparation de la Phase 2 — décision GO / NO-GO

> 18 novembre 2026 · à lire avec [`VALIDATION-PHASE1.1.md`](VALIDATION-PHASE1.1.md)

## Décision

<!-- DECISION -->
## **NO-GO pour la Phase 2 aujourd'hui — GO pour le lot d'intégration 1.2.**

Mise à jour du 18 novembre 2026, 15 h 12, après l'exécution n° 4 (30/30).

### Ce qui est désormais prouvé

L'infrastructure est validée de bout en bout sur votre poste Windows : 4 services Docker sains, persistance, **Alembic réel** (montée, idempotence, retour arrière, réapplication), **pgvector 0.8.7** avec index HNSW, 46 tests de base de données et de règles métier sur PostgreSQL réel, 58 tests d'accessibilité, aucun secret dans les journaux. Les trois critères d'infrastructure (C1–C3) sont remplis.

### Pourquoi ce n'est pas encore GO pour la Phase 2

1. **Aucune application exécutable** : ni API, ni interface. La Phase 2 du plan suppose ce socle (lot 1.2 ci-dessous).
2. **CI GitHub non exécutée sur les corrections** : la branche `phase-1.1` (6 commits) n'est pas poussée ; votre autorisation est requise.
3. **Un seul dossier de travail** : `C:\NurseLearnAI` (copie hors Git) reste à archiver.

## Critères de passage (tous requis)

| # | Critère | Preuve attendue | État |
|---|---|---|---|
| C1 | Services Docker démarrés et sains (PostgreSQL+pgvector, Redis, stockage S3 RustFS, Mailpit), volumes persistants, aucun secret dans les journaux | Journal `docs/validation-logs/phase1.1-*.log` sans FAIL | <!-- C1 -->✅ exécution n° 4 : 30/30 |
| C2 | `alembic upgrade head` → 60 tables, pgvector, HNSW, 0003 ; `upgrade head` idempotent ; `downgrade base` → base vide ; réapplication OK | Même journal + job CI « Alembic réel » | <!-- C2 -->✅ exécution n° 4 (sur votre poste) · ⏳ job CI |
| C3 | Suite pytest complète **avec** pgvector, 0 test ignoré | Même journal + job CI « Schéma » | <!-- C3 -->✅ 46 réussis, 0 ignoré, pgvector réel (exécution n° 4) |
| C4 | Branche `phase-1.1` poussée, CI GitHub verte (4 jobs) | Page Actions | ⏳ (votre autorisation de push) |
| C5 | Lot d'intégration 1.2 livré et vérifié (ci-dessous) | Tests + démonstration | ⏳ |
| C6 | Un seul dossier de travail local (`C:\NurseLearnAI-git`) | — | ⏳ (vous) |

Non bloquant pour la Phase 2, mais bloquant pour toute **publication** : au moins un relecteur médical qualifié (Québec) inscrit avec le rôle `medical_reviewer`.

## Lot d'intégration 1.2 — « socle exécutable » (proposé)

Objectif : une application qui démarre et permet de **choisir son pays et son métier puis de parcourir le catalogue**, sans les fonctionnalités avancées de la Phase 2.

| Inclus | Exclu (reste en Phase 2) |
|---|---|
| API FastAPI : configuration, erreurs RFC 9457, journaux JSON sans données personnelles, `/health` et `/ready` (PostgreSQL + Redis), OpenAPI | Inscription, connexion, sessions, RBAC applicatif |
| Modèles SQLAlchemy alignés sur le schéma + test `alembic check` (aucune divergence) | Outils auteur, relecture, publication |
| Lecture seule : `GET /api/v1/jurisdictions`, `/jurisdictions/{code}/professions`, `/specialties`, `/programs`, `/modules/{id}` (contenu **publié** uniquement) | Quiz, tentatives, progression, tableau de bord |
| Dockerfile API + service `api` dans docker-compose (profil `app`) | Tuteur IA, simulations, paiements |
| Next.js : accueil (avec mention « formation, pas un diplôme »), choix du pays, choix du métier, catalogue vide/état « contenu en préparation », FR/EN, thèmes clair/sombre | Lecteur de leçons complet |
| Structure du module pilote « Hygiène des mains et prévention des infections » pour PAB Québec : module, 4 leçons, objectifs — **statut DRAFT, sans contenu médical rédigé** | Rédaction et relecture du contenu médical |
| Tests : unitaires API, contrat OpenAPI, e2e Playwright (accueil → pays → métier → catalogue), audit axe ; jobs CI correspondants | — |

Estimation : 2 à 3 semaines pour un développeur. Ce lot correspond aux lots 2.1, 2.2 et à une partie de 2.4/2.7 du plan de Phase 2 ; il les avance, il ne les duplique pas.

## Séquence recommandée

1. ~~Vous lancez `scripts/validate-phase1.1.ps1` (C1–C3)~~ — fait, 30/30.
2. Vous autorisez le push de la branche `phase-1.1` ; je vérifie la CI GitHub (C4).
3. Vous validez le lot 1.2 ; je le réalise et le vérifie (C5).
4. Décision GO Phase 2.

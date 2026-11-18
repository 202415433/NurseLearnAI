# Préparation de la Phase 2 — décision GO / NO-GO

> Mise à jour du 18 novembre 2026, après l'intégration des Phases 1.1 et 1.2 dans `main` · à lire avec [`VALIDATION-PHASE1.1.md`](VALIDATION-PHASE1.1.md) et [`VALIDATION-PHASE1.2.md`](VALIDATION-PHASE1.2.md)

## Décision

<!-- DECISION -->
## **GO technique pour la Phase 2**, sous réserve de votre validation explicite. La Phase 2 n'est pas commencée.

Tous les critères techniques sont remplis : validation sur Windows (30/30, puis 52/52), CI GitHub verte sur chaque branche et sur `main` après chaque fusion, migrations intactes, aucune donnée supprimée, aucun contenu médical publié.

Deux conditions restent hors technique et ne bloquent pas le **démarrage** de la Phase 2 :
- **publication** de tout contenu médical : bloquée tant qu'aucun relecteur qualifié n'est inscrit (rôle `medical_reviewer`) ;
- archivage de l'ancien dossier `C:\NurseLearnAI` : procédure prête ([`ARCHIVAGE-ANCIEN-DOSSIER.md`](ARCHIVAGE-ANCIEN-DOSSIER.md)), à exécuter par vous.

## Critères de passage

| # | Critère | Preuve | État |
|---|---|---|---|
| C1 | Services Docker sains, volumes persistants, aucun secret dans les journaux | `validate-phase1.1.ps1` 30/30 ; `validate-phase1.2.ps1` 52/52 | ✅ |
| C2 | Alembic réel : `upgrade head` (60 tables, pgvector, 0003), idempotence, `downgrade base`, réapplication | Votre poste + job CI « Alembic réel » sur `main` | ✅ |
| C3 | Suite pytest complète avec pgvector, 0 test ignoré | 92/92 sur votre poste (Phase 1.2) | ✅ |
| C4 | `phase-1.1` poussée, CI verte | Run 44391779337 (push) et 44420679744 (PR #1) : 4/4 | ✅ |
| C5 | Phase 1.2 livrée et vérifiée | 52/52 sur Windows ; runs 44418899486, 44420983692 (push) et 44420989217 (PR #2) : 6/6 | ✅ |
| C6 | Un seul dossier de travail (`C:\NurseLearnAI-git`) | Toute la Phase 1.2 y a été faite ; procédure d'archivage de l'ancien dossier prête | ✅ · ⏳ archivage (vous) |
| C7 | Fusions vers `main` après CI verte | PR #1 → `dfca912`, PR #2 → `315d83b` ; run 44422659050 sur `main` : 6/6 | ✅ |

## Intégration dans `main` (18/11/2026)

| Étape | Commit | CI |
|---|---|---|
| Phase 1 (base) | `79ef00f` | run 44379561567 ✅ |
| PR #1 — Phase 1.1 (5 commits, fusion avec commit de fusion) | `dfca912` | PR : 4/4 ✅ |
| Mise à jour de `phase-1.2` avec `main` (sans conflit, contenu inchangé) | `225fa97` | push 6/6 ✅ · PR #2 6/6 ✅ |
| PR #2 — Phase 1.2 (8 commits + mise à jour depuis `main`) | `315d83b` | `main` : run 44422659050, 6/6 ✅ |

Historique préservé (fusions sans réécriture ni écrasement), aucune protection contournée (la branche `main` n'en a pas ; les fusions sont passées par des PR après CI verte).

## Résultats de la CI finale sur `main` (run 44422659050, `315d83b`)

| Job | Résultat |
|---|---|
| Schéma PostgreSQL + règles métier | ✅ 45 + 4 tests |
| Alembic réel (upgrade / downgrade / upgrade) | ✅ `0003_business_rules (head)` ; « 60 tables, 134 clés étrangères, 129 index, 33 déclencheurs, pgvector 0.8.7 » |
| API FastAPI (PostgreSQL + Redis réels) | ✅ 43 tests (dont paramétrés) |
| Web Next.js | ✅ Vitest 18/18, TypeScript, build, Playwright + axe **8/8** (dont le programme publié de démonstration) |
| Design system (contrastes WCAG) | ✅ 58 contrôles |
| Docker Compose (pile complète) | ✅ 6 services sains, API, Web, appel interne, CORS, persistance |

## Migrations

- Aucune migration modifiée par la Phase 1.2 (`git diff afe5479 cbdc4e4 -- services/api/alembic database/schema` vide).
- Les révisions 0001 et 0002 ont été modifiées pendant la Phase 1.1, **avant toute application réussie**, uniquement dans leur mécanisme d'exécution (A10/A13 : envoi du SQL sans paramètres) ; le SQL du schéma n'a pas changé. 0003 a été ajoutée en Phase 1.1.
- Votre base principale a été migrée pour la première fois par l'API au démarrage (exécution n° 3) : `0003_business_rules`.

## Anomalies et points ouverts (non bloquants)

| Sujet | Action proposée en Phase 2 |
|---|---|
| `npm ci` signale 5 vulnérabilités (2 modérées, 1 élevée, 2 critiques) dans les dépendances web | Audit `npm audit` et mise à jour ciblée (premier lot de la Phase 2) |
| Avertissement Starlette : `httpx` déprécié pour le client de test | Suivre la recommandation lors de la mise à jour des dépendances |
| Limite de débit globale (le Web appelle l'API pour tous les visiteurs) | Proxy d'entrée + `X-Forwarded-For` de confiance |
| Modèles SQLAlchemy limités aux tables lues | Modélisation complète + `alembic check` en CI |
| CSP stricte, linters (Ruff, ESLint) | À brancher en CI |
| Statut HTTP 200 + `noindex` sur certaines pages introuvables (diffusion en flux) | Accepté ; à revoir avec le rendu des erreurs |
| Ports locaux : 8000 et 18000 occupés par d'autres projets | NurseLearn publie l'API sur 28000 (`API_PORT`) |
| Relecteur médical | Inscrire au moins un relecteur qualifié avant toute publication |

## Prochaine étape

Attendre votre validation explicite avant de commencer la Phase 2 (plan : [`07-plan-phase-2.md`](07-plan-phase-2.md)).

# Rapport de validation — Phase 1.2 (API publique et parcours web)

> 18 novembre 2026 · branche `phase-1.2` (locale, **non poussée**) · statut : **développée — validation réelle en attente**

## 1. Point de départ : Phase 1.1 validée

| Preuve | Résultat |
|---|---|
| `scripts/validate-phase1.1.ps1` sur le poste Windows | 30/30, deux exécutions |
| CI GitHub, branche `phase-1.1` (`afe5479`), run 44391779337 | 4/4 jobs réussis |
| Fusion vers `main` | non effectuée (autorisation requise) |

## 2. Périmètre livré

| Exigence | Livré |
|---|---|
| Backend FastAPI modulaire : configuration, SQLAlchemy asynchrone, erreurs RFC 9457, validation Pydantic, `/api/v1`, OpenAPI, journaux JSON, `/health`, `/ready` | `services/api/app` (`core/`, `db/`, `domains/reference`, `domains/catalog`) |
| `GET /countries`, `/jurisdictions`, `/professions`, `/specialties`, `/programs`, `/programs/{id}` avec pagination, filtres et schémas de réponse | voir [`API-REFERENCE.md`](API-REFERENCE.md) |
| Écrans : accueil, pays, juridiction, métier, spécialités, programmes, détail, erreur, chargement, introuvable | voir [`FRONTEND-ARCHITECTURE.md`](FRONTEND-ARCHITECTURE.md) |
| Parcours Pays → Juridiction → Métier → Spécialité → Programme pour Québec, Ontario, France, Sénégal (et référentiels transnationaux) | données issues de PostgreSQL, aucune liste codée en dur |
| Programmes non validés clairement signalés | badge « N programme(s) en préparation, non encore validé(s) » ; aucun contenu exposé |
| Docker : `docker compose up --build -d` démarre toute la pile | `compose.yaml` à la racine (inclut `infra/docker-compose.yml`) ; PostgreSQL, Redis, RustFS et Mailpit **inchangés**, volumes conservés (projet `nurselearn`) ; santé `api` et `web` |
| Sécurité | validation stricte, erreurs sans détail technique, GET uniquement, filtrage du non-publié, isolation des établissements, aucun secret dans les réponses, CORS restreint, en-têtes de sécurité, limite de débit |
| Aucune fausse connexion | aucun écran ni point d'accès d'authentification |

Hors périmètre (règles respectées) : Phase 2, authentification, tuteur IA, paiements, application mobile, publication de contenu médical. Aucune migration historique modifiée, aucune donnée supprimée.

## 3. Tests

| Suite | Contenu | Où elle s'exécute |
|---|---|---|
| `database/tests` | schéma, 12 règles métier, migrations, structure du pilote (brouillon) | espace de travail ✅ · script · CI |
| `services/api/tests/unit` (5 tests) | négociation de langue, repli i18n, pagination, pilotes de base, journal JSON | script · CI |
| `services/api/tests/api` (21 tests, dont paramétrés) | santé, OpenAPI GET seul, pays, juridictions et filtres, pagination, 422 stricts, métiers par juridiction (QC/ON/FR/SN), disponibilité, spécialités, **brouillons invisibles**, chemin publié (données fictives non médicales créées puis supprimées), **isolation des établissements**, aucun secret, 500 générique, limite de débit, CORS, modèles ↔ schéma | script · CI (PostgreSQL + Redis réels) |
| `apps/web/tests` (Vitest, 18 tests) | composants (badges, fil d'Ariane, cartes, pagination, états vides), client API, parité des dictionnaires FR/EN, mention obligatoire | script · CI |
| `apps/web/e2e` (Playwright + axe, 8 scénarios) | parcours Québec complet, ON/FR/SN, référentiels, changement de langue, thème sombre, paramètres invalides → introuvable, détail d'un programme publié (données de démonstration **CI uniquement**) | script · CI |
| `scripts/validate-phase1.2.ps1` (52 contrôles) | ports libres, pile complète, santé des 6 services, API, CORS, ports Windows, communication interne, persistance après redémarrage, migrations, toutes les suites ci-dessus, secrets | votre PC |
| CI `compose` | `docker compose up --build -d --wait`, API, Web, appel interne web → api, CORS, persistance | GitHub (après push) |

Les données de démonstration du test de détail (`database/fixtures/e2e_demo_published.sql`) sont fictives, non médicales, rattachées à Ontario/PSW, et **refusent de s'exécuter** sans `nl.allow_demo=on` : elles ne peuvent pas être chargées par erreur en développement ou en production.

### Exécuté dans l'espace de travail

| Contrôle | Résultat |
|---|---|
| `database/tests` + `services/api/tests` sur PostgreSQL 16 réel (sans pgvector) | ✅ 48 réussis, 3 ignorés (pgvector ; tests API et unitaires faute de FastAPI installable ici) |
| Fixture de démonstration : refus sans autorisation, idempotence (2 passages) | ✅ |
| Vérification TypeScript stricte de tout le code web (pages, composants, tests) contre des déclarations minimales de React/Next | ✅ aucune erreur |
| Exécution réelle de `lib/i18n.ts` et `lib/api.ts` (parité FR/EN, construction des adresses, erreurs) | ✅ |
| YAML du workflow et du compose | ✅ |

### Non exécuté ici (aucun paquet Python/Node installable dans l'espace de travail)

Tests FastAPI, Vitest, `next build`, Playwright/axe, construction des images Docker. Ils seront prouvés par `scripts/validate-phase1.2.ps1` sur votre PC **ou** par la CI GitHub après push de `phase-1.2` (**autorisation requise**).

<!-- RESULTATS -->

### Exécution n° 1 sur votre poste (18/11/2026, 16 h 22) — 13/50

| Constat | Cause | Correction (commit suivant) |
|---|---|---|
| ✅ `pytest_complet` : **92 réussis** (base, migrations, API, unitaires, pgvector) | — | — |
| ✅ Vitest : **18/18** ; vérification TypeScript ✅ ; images `api` et `web` construites (`next build` ✅) | — | — |
| ❌ 1er `docker compose up` : « Bind for 127.0.0.1:8000 failed: port is already allocated » | Un **autre programme** écoute déjà sur 127.0.0.1:8000 (il répond 404 à tout : d'où les faux résultats 404/CORS) | Port de l'API publié par défaut sur **18000** (`API_PORT`) ; le script identifie désormais le programme qui occupe un port |
| ❌ API « unhealthy » : `failed to resolve host 'postgres'` | Le conteneur `api` resté de la tentative échouée a été réutilisé sans réseau valide | Le script retire les conteneurs `api`/`web` (sans données) avant le démarrage ; l'API réessaie ses migrations 10 fois |
| ❌ Web et Playwright (`web:3000` introuvable) | Conséquence : le Web attend une API saine | Résolu par les deux points ci-dessus |
| ℹ️ `professions` absente de la base principale | La base principale n'avait jamais été migrée (la Phase 1.1 utilisait des bases jetables) ; l'API la migre au démarrage | — |

## 4. Critères d'acceptation

| # | Critère | Preuve | État |
|---|---|---|---|
| 1 | `docker compose up --build -d` démarre l'application | script `compose_up_build`, `sante_*` · CI `compose` | ⏳ |
| 2 | FastAPI répond | `api_health`, `api_ready_pg_redis` | ⏳ |
| 3 | Next.js accessible | `web_accueil` | ⏳ |
| 4 | Données issues de PostgreSQL | `donnees_issues_de_postgresql` (compte API = compte SQL) | ⏳ |
| 5–9 | Pays, juridiction, métier, spécialités, programmes publics | `web_*`, e2e parcours | ⏳ |
| 10 | Brouillons invisibles | `api_brouillon_invisible_*`, `web_*_brouillon_*`, tests API et base (✅ côté base) | ⏳ |
| 11 | Données d'établissement isolées | `test_organization_content_is_isolated` | ⏳ |
| 12 | Migrations compatibles | `migrations_a_jour`, aucune migration modifiée, tests de migrations (✅ ici) | ⏳ |
| 13 | Tests réussis | `pytest_complet`, `web_tests_vitest_playwright_axe`, CI | ⏳ |
| 14 | Commandes Windows documentées | README §« Démarrer », ce document | ✅ |

## 5. Commandes Windows

```powershell
cd C:\NurseLearnAI-git
git switch phase-1.2
docker compose up --build -d                      # toute la pile
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase1.2.ps1
start http://localhost:3000/fr                    # interface
start http://localhost:18000/docs                  # API (OpenAPI)
docker compose ps                                 # état et santé
docker compose logs -f api web                    # journaux
docker compose stop                               # arrêt SANS perte de données
```

Ne pas utiliser `docker compose down -v` (supprimerait les volumes de données).

## 6. Écarts assumés et points ouverts

- **Limite de débit** : le serveur Web appelle l'API pour tous les visiteurs ; en local la limite est donc globale (3 000/min par défaut). La limite par visiteur se fera au proxy d'entrée avec en-têtes `X-Forwarded-For` de confiance (Phase 2, lot déploiement).
- **Statut HTTP des pages introuvables** : avec l'état de chargement, Next.js peut répondre 200 + `noindex` au lieu de 404 ; l'écran affiché est correct.
- **Modèles SQLAlchemy** limités aux 10 tables lues ; modélisation complète et `alembic check` en Phase 2.
- **`package-lock.json`** sera créé au premier `npm install` et devra être versionné.
- **CSP stricte** et **linters** (Ruff, ESLint) : à brancher en Phase 2.
- **Contenu médical** : le pilote PAB Québec reste en brouillon tant qu'aucun relecteur qualifié n'est inscrit.

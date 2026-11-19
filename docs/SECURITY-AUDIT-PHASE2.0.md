# Audit de sécurité — Phase 2.0

> 18 novembre 2026 · branche `phase-2.0-security-quality` (locale, non poussée) · à lire avec [`VALIDATION-PHASE2.0.md`](VALIDATION-PHASE2.0.md)

## 1. Dépendances npm (`apps/web`)

### Commande exécutée (votre poste, 18/11/2026, 20 h 18, lockfile de la Phase 1.2)

```powershell
docker run --rm -v "${PWD}:/repo" -w /repo/apps/web node:22-bookworm sh -c "npm audit --package-lock-only --json > /repo/docs/validation-logs/npm-audit.json; npm audit --package-lock-only --omit=dev --json > /repo/docs/validation-logs/npm-audit-prod.json"
```

Résultat : **5 vulnérabilités — 2 critiques, 1 élevée, 2 modérées** (production seule : 1 élevée, 1 modérée).

### Analyse

| Paquet (version) | Gravité | Chaîne | Prod / dev | Avis | Exploitabilité dans NurseLearn |
|---|---|---|---|---|---|
| `tinypool` 1.1.1 | **Critique** (×2) | `vitest@3.2.7` → `tinypool` | dev | GHSA-5gmw-xhrv-c9v3, GHSA-85c8-ppgw-ccpr : pollution de prototype → exécution de code via les options du pool de travailleurs | Faible : outil de test uniquement, jamais présent dans l'image de production (sortie `standalone` de Next.js) ; options contrôlées par notre configuration. Corrigé quand même : un outil de développement compromis atteint le poste et la CI. |
| `vitest` 3.2.7 | **Critique** (hérité) + modérée | direct | dev | GHSA-82fw-gwwq-j7x9 (lecture de fichiers via redirection de simulation) | Faible : serveur de test local non exposé |
| `@vitest/mocker` 3.2.7 | Modérée (×2) | `vitest` → `@vitest/mocker` | dev | GHSA-82fw-gwwq-j7x9, GHSA-qxg6-89mg-rqv8 (injection via enregistrement de simulation non authentifié) | Faible : idem |
| `postcss` 8.4.31 | **Élevée** (×2) + modérées | `next@15.5.29` → `postcss` (version figée par Next.js) | prod (étape de construction) | GHSA-6g55-p6wh-862q, GHSA-r28c-9q8g-f849 (lecture de fichiers `.map` via `sourceMappingURL`), GHSA-fxqj-rqcc-2cmp, GHSA-qx2v-qp2m-jg93 (XSS dans la sortie CSS) | Faible : PostCSS ne traite que **nos** feuilles de style, à la construction ; aucun CSS fourni par un utilisateur, aucun traitement à l'exécution. Corrigé quand même : la chaîne d'approvisionnement de la construction doit être saine. |
| `next` 15.5.29 | Modérée (héritée de `postcss`) | direct | prod | — | Résolue avec `postcss` |

### Corrections appliquées (sans `npm audit fix --force`)

| Problème | Correctif proposé par `npm audit` | Correctif retenu | Pourquoi |
|---|---|---|---|
| `vitest` / `tinypool` / `@vitest/mocker` | `vitest@5.1.1` (2 versions majeures) | **`vitest@^4.1.12`** (1 version majeure) | Plus petite version corrigeant tous les avis (`@vitest/mocker` ≥ 4.1.12, plus de `tinypool` vulnérable) ; compatible avec notre configuration (`esbuild.jsx`, environnements par fichier, `vi.mock`). |
| `postcss` figé à 8.4.31 par Next.js | `next@16.4.6` (version majeure, migration App Router/cache) | **`overrides: { "next": { "postcss": "^8.5.33" } }`** (limité à l'arbre de Next.js) | Même version majeure de PostCSS (8.x, API stable), déjà utilisée par Tailwind dans le projet ; évite une migration Next.js 16 non planifiée. npm n'appliquant pas un override à une entrée déjà verrouillée, `scripts/npm-lock.sh` retire cette entrée avant résolution puis vérifie que toutes les copies de postcss sont ≥ 8.5.23. |
| Version minimale de Next.js | — | **`next@^15.5.29`**, `eslint-config-next@^15.5.29` | Empêche un futur lockfile de redescendre sous la dernière version corrigée de la branche 15.5 (versions 15.5.18, .21, .24, .28 et .29 : correctifs de sécurité de 2026). |

Les dépendances nécessaires n'ont pas été retirées et l'audit n'a pas été désactivé : un job CI `dependency-audit` échoue désormais pour toute vulnérabilité **élevée ou critique** (`npm audit --audit-level=high`) et `pip-audit --strict` contrôle l'API.

**Vérification (exécution n° 5, 18/11/2026, 21 h 41)** : `npm audit --package-lock-only` → **found 0 vulnerabilities** (515 dépendances : critique 0, élevée 0, modérée 0, faible 0) ; versions résolues : `next` 15.5.29, `postcss` 8.5.33 (copie unique, plus de copie imbriquée sous Next.js), `vitest` 4.1.12, `vite` 8.3.8, plus de `tinypool`. Non-régression complète réussie : [`VALIDATION-PHASE2.0.md`](VALIDATION-PHASE2.0.md).

**Note sur l'outillage** : npm 10 (fourni avec Node 22) échoue sur la mise à niveau vitest 3 → 4 (« Cannot read properties of null (reading 'edgesOut') », dans `#loadPeerSet`). Le lockfile a été produit par npm 11 puis accepté par `npm ci` de npm 10 (image Docker et CI).

### Risque restant

- **Next.js 15** : support de sécurité annoncé jusqu'au 31 janvier 2027. Prévoir la migration vers Next.js 16 avant cette date (proposé au lot 2.5, ou plus tôt si un avis critique n'est corrigé que sur 16).
- **Override `postcss`** : à retirer dès qu'une version 15.5.x de Next.js embarque une version corrigée.

## 2. Dépendances Python (`services/api`)

`pip-audit --strict --disable-pip --no-deps -r <pip freeze --exclude-editable>` est exécuté sur l'environnement réel de l'API (dépendances et outils de développement) par le script de validation et par la CI. Résultat du 18/11/2026 : **No known vulnerabilities found**. Versions installées constatées en CI : FastAPI 0.165.0, Starlette 1.10.0, Pydantic 2.14.3, SQLAlchemy 2.1.9, asyncpg 0.32.0, psycopg 3.3.9, Alembic 1.20.1, redis 6.4.0, uvicorn 0.54.0.

## 3. Sécurité HTTP

| Point | Constat avant | Correction | Test |
|---|---|---|---|
| CSP (Web) | Aucune | CSP par requête : `script-src 'self' 'nonce-…' 'strict-dynamic'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'` ; `'unsafe-eval'` seulement en développement ; `upgrade-insecure-requests` en HTTPS. Le script de thème porte le nonce. | `e2e/securite.spec.ts` (nonce unique, aucune violation CSP pendant le parcours), `tests/security.test.ts` |
| CSP (API) | Aucune | `default-src 'none'; frame-ancestors 'none'` sauf `/docs` | `test_security_headers_and_csp` |
| Autres en-têtes | nosniff, Referrer-Policy | + `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-site` (API) ; sans `X-Powered-By` (Web) ni `Server` (API, `--no-server-header`) | script 2.0, e2e |
| 404 | Pages introuvables servies en **200** + `noindex` (réponse diffusée avant le rendu à cause de `loading.tsx`) | `loading.tsx` retiré ; état de chargement affiché dans le lien cliqué (`useLinkStatus`) ; **404 réel** | `parcours.spec.ts`, `securite.spec.ts`, script 2.0 |
| Erreurs | 500 générique, problem+json | + en-tête `Allow` sur les 405 ; aucune trace ni nom interne | `test_unknown_route_and_method_are_generic` |
| Limite de débit | Globale (le serveur Web portait toutes les requêtes) ; sel aléatoire par processus (incompatible avec plusieurs instances) | Par visiteur ; sel partagé (`RATE_LIMIT_SALT`, dérivé par défaut de la configuration) ; `INCR` + `EXPIRE` atomiques ; en-têtes `RateLimit-Limit/Remaining` ; `X-Forwarded-For` lu uniquement depuis un mandataire approuvé (`TRUSTED_PROXIES`, nom d'hôte `web` résolu dynamiquement) en retenant l'adresse non approuvée la plus à droite | `test_rate_limit*`, `test_forwarded_for_ignored_from_untrusted_peer`, `tests/unit/test_clientip.py` (6), script 2.0 |
| CORS | Origines configurées, GET | + `max_age`, en-têtes `RateLimit-*` exposés ; `*` interdit en production | `test_production_rejects_wildcard_cors` |
| Informations internes | `/docs` et `/openapi.json` toujours publics | Masqués en production (sauf `DOCS_ENABLED=true`) | `test_docs_hidden_in_production` |

### Stratégie de limitation de débit en production (préparée, sans dépendance externe en local)

1. **Plusieurs instances d'API** : compteurs Redis partagés et sel commun (`RATE_LIMIT_SALT` identique sur toutes les instances).
2. **Reverse proxy** : le proxy d'entrée doit **remplacer** `X-Forwarded-For` par l'adresse du client (ex. nginx `proxy_set_header X-Forwarded-For $remote_addr;`) ; `TRUSTED_PROXIES` de l'API = adresses du serveur Web et/ou du proxy.
3. **Limites spécifiques** (lot 2.1) : connexion, inscription et réinitialisation, par adresse **et** par compte.

### Risques restants

| Risque | Gravité | Traitement |
|---|---|---|
| `style-src 'unsafe-inline'` (styles injectés par Next.js) | Faible : aucun style fourni par l'utilisateur | Réévaluer avec Next.js 16 |
| Web exposé **sans** reverse proxy : Next.js conserve un `X-Forwarded-For` fourni par le visiteur, qui peut alors répartir ses requêtes sur plusieurs compteurs | Moyen en exposition directe, nul en local | Topologie de production avec reverse proxy (étape 2 ci-dessus) ; limites par compte au lot 2.1 |
| HSTS non envoyé | Sans objet en HTTP local | À activer au reverse proxy en production |
| Rendu dynamique de toutes les pages (nonce par requête) | Performance | Acceptable à ce stade ; cache côté API (Redis) prévu |

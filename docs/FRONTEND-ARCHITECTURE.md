# Architecture de l'interface web — Phase 1.2

> `apps/web` · Next.js 15 (App Router), React 19, TypeScript strict, Tailwind CSS 4

## Principes

- **Rendu serveur** : chaque page est un composant serveur qui appelle l'API (`API_INTERNAL_URL`, réseau Docker interne). Le navigateur ne contacte jamais l'API et ne reçoit aucun secret. Aucune liste n'est codée en dur : pays, juridictions, métiers, spécialités et programmes viennent de PostgreSQL via l'API.
- **Aucune fausse connexion** : pas d'écran de connexion ni de session avant la Phase 2.
- **Honnêteté du catalogue** : un programme non publié n'est jamais affiché ; un métier dont les programmes sont en préparation porte le badge « en préparation, non encore validé ». La mention « ni un diplôme, ni une licence, ni une autorisation d'exercer » figure dans le pied de page et sur chaque programme.
- **Aucune équivalence** : un métier n'est cherché que dans sa propre juridiction ; une combinaison incohérente (ex. `psw` au Québec) donne une page introuvable.

## Parcours et adresses

| Étape | Adresse | Données (API) |
|---|---|---|
| Accueil | `/fr`, `/en` | — |
| Pays | `/fr/pays` | `GET /countries` + carte « Autres référentiels » |
| Juridiction | `/fr/juridictions?pays=CA` · `?portee=cadre` | `GET /jurisdictions?country=` · `?scope=framework` |
| Métier | `/fr/metiers?j=CA-QC` | `GET /jurisdictions`, `GET /professions?jurisdiction=` |
| Spécialités | `/fr/specialites?j=CA-QC&p=pab` | + `GET /specialties?jurisdiction=&profession=` |
| Programmes | `/fr/programmes?j=CA-QC&p=pab[&s=…][&offset=…]` | + `GET /programs?...` (20 par page) |
| Détail | `/fr/programmes/{uuid}` | `GET /programs/{id}` |

Les adresses ne contiennent que des codes publics ; elles sont partageables et fonctionnent sans JavaScript (liens, pagination par liens). Le sélecteur de langue conserve le chemin et les paramètres.

## Organisation du code

```
apps/web/
├─ middleware.ts              redirection / → /fr|/en (Accept-Language) ; en-tête x-nl-locale
├─ app/
│  ├─ globals.css             Tailwind 4 + jetons du design system (thèmes clair/sombre)
│  └─ [locale]/
│     ├─ layout.tsx           <html lang>, en-tête (langue, thème), lien d'évitement, pied de page (mention)
│     ├─ page.tsx             accueil
│     ├─ pays/ juridictions/ metiers/ specialites/ programmes/ programmes/[id]/
│     ├─ loading.tsx          état de chargement (role="status")
│     ├─ error.tsx            erreur générique, bouton « Réessayer », aucun détail technique
│     ├─ not-found.tsx        page introuvable localisée
│     └─ [...rest]/page.tsx   adresses inconnues → introuvable
├─ components/                Breadcrumbs, ChoiceCard, AvailabilityBadge, Pagination, EmptyState,
│                             Notice, LanguageSwitcher, ThemeToggle
├─ lib/
│  ├─ api.ts                  client typé (Page<T>), délai 5 s, validation des paramètres d'URL
│  ├─ route.ts                langue, paramètre unique, 404/422 → introuvable, résolution juridiction/métier
│  └─ i18n.ts                 dictionnaires FR/EN (même forme, vérifiée par test)
├─ tests/                     Vitest + Testing Library (composants, client API, dictionnaires)
└─ e2e/                       Playwright + axe-core (parcours, langue, thème, erreurs, accessibilité)
```

## Gestion des erreurs

| Cas | Comportement |
|---|---|
| Paramètre invalide dans l'adresse | Validé avant l'appel (`lib/api.ts`), page introuvable |
| API `404` / `422` | Page introuvable |
| API injoignable, `5xx`, délai dépassé | `error.tsx` : message générique et bouton « Réessayer » |
| Chargement | `loading.tsx` (squelette) |

Avec un état de chargement, Next.js diffuse la page en flux : une page introuvable peut garder le statut HTTP 200 (Next.js ajoute alors `noindex`). Les tests vérifient donc l'écran affiché.

## Accessibilité (WCAG 2.2 AA)

Langue du document, lien d'évitement, fil d'Ariane (`aria-current`), cibles ≥ 44 px, focus visible, contrastes vérifiés par le design system dans les deux thèmes, états annoncés (`role="status"`, `role="alert"`), contenu lisible sans JavaScript. Chaque page du parcours est auditée par axe-core (aucune violation « serious » ou « critical » tolérée).

## Sécurité

En-têtes définis dans `next.config.mjs` (`X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, sans `X-Powered-By`). Une politique CSP stricte (avec nonce pour le script de thème) est prévue en Phase 2, sortie `standalone`, conteneur non root, `cache: "no-store"` sur les appels API.

## Commandes

```powershell
cd apps\web
npm install
npm run typecheck
npm test                 # Vitest
npm run build
$env:E2E_BASE_URL="http://localhost:3000"; npx playwright test   # contre la pile Docker démarrée
```

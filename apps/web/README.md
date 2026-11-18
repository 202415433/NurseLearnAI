# apps/web — Interface web NurseLearn AI (Next.js)

Phase 1.2 : accueil, pays, juridiction, métier, spécialités, programmes, détail d'un programme, états d'erreur et de chargement.
FR/EN (`/fr`, `/en`), thèmes clair/sombre, jetons de `packages/design-tokens`, objectif WCAG 2.2 AA.
Architecture : [`docs/FRONTEND-ARCHITECTURE.md`](../../docs/FRONTEND-ARCHITECTURE.md).

```powershell
cd apps\web
npm install
$env:API_INTERNAL_URL = "http://localhost:8000"   # API démarrée (docker compose up --build -d)
npm run dev                                        # http://localhost:3000
npm run typecheck; npm test                        # TypeScript + Vitest
npm run build; npx playwright install chromium; npm run test:e2e
```

`package-lock.json` sera créé au premier `npm install` : le valider dans Git pour figer les versions.

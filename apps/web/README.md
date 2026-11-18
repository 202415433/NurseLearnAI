# apps/web — Interface web NurseLearn AI (Next.js)

Lot 1.2 : accueil, choix du pays/de la province, choix du métier, catalogue (vide tant qu'aucun cours n'est publié).
FR/EN (`/fr`, `/en`), thèmes clair/sombre, jetons de `packages/design-tokens`, objectif WCAG 2.2 AA.

```powershell
cd apps\web
npm install
$env:API_INTERNAL_URL = "http://localhost:8000"   # API démarrée (docker compose --profile app)
npm run dev                                        # http://localhost:3000
npm run build; npx playwright install chromium; npm run test:e2e
```

`package-lock.json` sera créé au premier `npm install` : le valider dans Git pour figer les versions.

# @nurselearn/design-tokens

Source unique : `src/tokens.json`. `npm run build` génère `dist/tokens.css` (variables CSS, thèmes clair/sombre), `dist/tailwind-preset.cjs` et `dist/tokens.native.json` (React Native). `npm test` vérifie les contrastes WCAG 2.2 AA de toutes les paires déclarées dans les deux thèmes. Ne pas modifier `dist/` à la main.

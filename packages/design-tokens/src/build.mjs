// Génère dist/tokens.css (variables CSS, thèmes clair/sombre) et
// dist/tailwind-preset.cjs à partir de src/tokens.json. Aucune dépendance.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const t = JSON.parse(readFileSync(join(here, "tokens.json"), "utf8"));
const out = join(here, "..", "dist");
mkdirSync(out, { recursive: true });

const vars = (obj, prefix) =>
  Object.entries(obj).map(([k, v]) => `  --nl-${prefix}${k}: ${v};`).join("\n");

const common = [
  vars(t.font.family, "font-"), vars(t.font.size, "text-"), vars(t.font["line-height"], "leading-"),
  vars(t.font.weight, "weight-"), vars(t.space, "space-"), vars(t.radius, "radius-"),
  vars(t.shadow, "shadow-"), vars(t.motion, "motion-"), vars(t.size, "size-"),
].join("\n");

const css = `/* Généré par src/build.mjs — ne pas modifier à la main. v${t.meta.version} */
:root {
${common}
${vars(t.color.light, "color-")}
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
${vars(t.color.dark, "color-").replace(/^/gm, "  ")}
    color-scheme: dark;
  }
}
[data-theme="dark"] {
${vars(t.color.dark, "color-")}
  color-scheme: dark;
}
/* Animations discrètes, désactivées si l'utilisateur le demande (WCAG 2.3.3). */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
/* Focus toujours visible (WCAG 2.4.7 / 2.4.13). */
:focus-visible { outline: 3px solid var(--nl-color-focus); outline-offset: 2px; }
`;
writeFileSync(join(out, "tokens.css"), css);

const colorMap = Object.fromEntries(Object.keys(t.color.light).map((k) => [k, `var(--nl-color-${k})`]));
const preset = `// Généré par src/build.mjs — preset Tailwind CSS partagé (web).
module.exports = {
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: ${JSON.stringify(colorMap, null, 6)},
      fontFamily: { sans: ["var(--nl-font-sans)"], mono: ["var(--nl-font-mono)"] },
      borderRadius: ${JSON.stringify(t.radius)},
      boxShadow: ${JSON.stringify(t.shadow)},
      minHeight: { touch: "${t.size["touch-target-min"]}" },
      minWidth: { touch: "${t.size["touch-target-min"]}" },
      maxWidth: { prose: "${t.size["content-max"]}" },
      transitionTimingFunction: { nl: "${t.motion.easing}" },
    },
  },
};
`;
writeFileSync(join(out, "tailwind-preset.cjs"), preset);

// Export JS pour React Native (pas de variables CSS côté mobile).
writeFileSync(join(out, "tokens.native.json"), JSON.stringify({
  color: t.color, font: t.font, space: t.space, radius: t.radius, size: t.size,
}, null, 2));
console.log("design-tokens : dist/ généré");

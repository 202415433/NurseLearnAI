// Vérifie les ratios de contraste WCAG 2.x de toutes les paires déclarées,
// dans les deux thèmes. Exécution : node --test "test/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

const t = JSON.parse(readFileSync(new URL("../src/tokens.json", import.meta.url), "utf8"));

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

test("formule de contraste conforme (noir/blanc = 21:1)", () => {
  assert.equal(ratio("#000000", "#FFFFFF").toFixed(1), "21.0");
});

test("les deux thèmes définissent exactement les mêmes jetons", () => {
  assert.deepEqual(Object.keys(t.color.light).sort(), Object.keys(t.color.dark).sort());
});

for (const theme of ["light", "dark"]) {
  for (const [fg, bg, min] of t.contrast_pairs) {
    test(`${theme} : ${fg} sur ${bg} ≥ ${min}:1`, () => {
      const r = ratio(t.color[theme][fg], t.color[theme][bg]);
      assert.ok(r >= min, `${r.toFixed(2)}:1 < ${min}:1 (${t.color[theme][fg]} / ${t.color[theme][bg]})`);
    });
  }
}

test("cible tactile minimale de 44px (WCAG 2.5.5 / 2.5.8)", () => {
  assert.ok(parseInt(t.size["touch-target-min"], 10) >= 44);
});

test("les fichiers générés existent et contiennent les deux thèmes", () => {
  const css = readFileSync(new URL("../dist/tokens.css", import.meta.url), "utf8");
  assert.match(css, /\[data-theme="dark"\]/);
  assert.match(css, /prefers-reduced-motion/);
  assert.ok(existsSync(new URL("../dist/tailwind-preset.cjs", import.meta.url)));
});

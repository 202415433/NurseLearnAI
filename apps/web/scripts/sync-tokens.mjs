// Copie les jetons de design générés (packages/design-tokens) dans l'application.
// Le fichier copié est ignoré par Git : la source unique reste packages/design-tokens.
import { copyFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "..", "packages", "design-tokens", "dist", "tokens.css");
const dest = join(here, "..", "app", "tokens.css");
if (!existsSync(src)) {
  console.error(`Jetons introuvables : ${src}. Lancez « npm test » dans packages/design-tokens.`);
  process.exit(1);
}
copyFileSync(src, dest);
console.log("design-tokens : tokens.css synchronisé");

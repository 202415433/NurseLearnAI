#!/bin/sh
# Mise à jour sûre de apps/web/package-lock.json (exécuté dans un conteneur node:22).
# - Travaille sur une COPIE (/tmp/web) : le lockfile du dépôt n'est remplacé qu'en cas de succès complet.
# - En cas d'échec, diagnostique la cause en essayant des variantes de package.json (sans rien écrire).
set -u
SRC=/repo/apps/web
FLAGS="--no-audit --no-fund --loglevel=error"

try_lock() { # $1 = dossier ; renvoie 0 si le lockfile est généré et cohérent
  (cd "$1" && npm install --package-lock-only $FLAGS > /tmp/npm.out 2>&1 && npm ci --dry-run $FLAGS > /dev/null 2>&1)
}
fresh_copy() {
  rm -rf "$1" && mkdir -p "$1" && cp "$SRC/package.json" "$1/" && cp "$SRC/package-lock.json" "$1/" 2>/dev/null
  return 0
}
log_tail() { tail -n 25 "$(ls -t /root/.npm/_logs/*-debug-0.log | head -1)" 2>/dev/null; }

fresh_copy /tmp/web
if try_lock /tmp/web; then
  cp /tmp/web/package-lock.json "$SRC/package-lock.json"
  echo "lock-ok (package.json du dépôt)"
  exit 0
fi
echo "ÉCHEC avec package.json du dépôt :"; cat /tmp/npm.out; echo "--- fin du journal npm :"; log_tail

echo; echo "=== Diagnostic (aucun fichier du dépôt modifié) ==="
node -e '
const fs=require("fs");const base=JSON.parse(fs.readFileSync("/repo/apps/web/package.json","utf8"));
const v=(o)=>JSON.stringify(Object.assign(JSON.parse(JSON.stringify(base)),o),null,2);
const noOv=JSON.parse(JSON.stringify(base));delete noOv.overrides;
fs.mkdirSync("/tmp/variants",{recursive:true});
fs.writeFileSync("/tmp/variants/sans-override.json",JSON.stringify(noOv,null,2));
const glob=JSON.parse(JSON.stringify(base));glob.overrides={postcss:"$postcss"};glob.devDependencies.postcss="^8.5.33";
fs.writeFileSync("/tmp/variants/override-global-reference.json",JSON.stringify(glob,null,2));
const old=JSON.parse(JSON.stringify(noOv));old.devDependencies.vitest="^3.2.0";
fs.writeFileSync("/tmp/variants/sans-override-vitest3.json",JSON.stringify(old,null,2));
const noLint=JSON.parse(JSON.stringify(base));for(const k of ["eslint","eslint-config-next","@eslint/eslintrc"])delete noLint.devDependencies[k];
fs.writeFileSync("/tmp/variants/sans-eslint.json",JSON.stringify(noLint,null,2));
'
for f in /tmp/variants/*.json; do
  name=$(basename "$f" .json)
  fresh_copy "/tmp/v-$name"; cp "$f" "/tmp/v-$name/package.json"
  if try_lock "/tmp/v-$name"; then r=OK; else r="ÉCHEC : $(grep -m1 'npm error' /tmp/npm.out)"; fi
  echo "variante $name : $r"
done
exit 1

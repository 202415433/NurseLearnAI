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
# Toutes les copies de postcss du lockfile doivent être corrigées (≥ 8.5.23, avis GHSA-fxqj-rqcc-2cmp).
check_postcss() {
  node -e '
    const d=JSON.parse(require("fs").readFileSync("/tmp/web/package-lock.json","utf8"));
    const bad=Object.entries(d.packages).filter(([k,v])=>/node_modules\/postcss$/.test(k)&&v.version)
      .filter(([k,v])=>{const [a,b,c]=v.version.split(".").map(Number);return a<8||(a===8&&(b<5||(b===5&&c<23)));});
    if(bad.length){console.log("postcss non corrigé : "+bad.map(([k,v])=>k+"@"+v.version).join(", "));process.exit(1);}
    console.log("postcss : toutes les copies ≥ 8.5.23");' > /tmp/postcss.out 2>&1 || { cat /tmp/postcss.out; return 1; }
  cat /tmp/postcss.out
}
log_tail() { tail -n 25 "$(ls -t /root/.npm/_logs/*-debug-0.log | head -1)" 2>/dev/null; }

# Diagnostic du 18/11 : l'anomalie npm 10 « reading 'edgesOut' » (build-ideal-tree #loadPeerSet) apparaît
# à la mise à niveau de vitest 3 → 4, indépendamment de l'override postcss. Tentatives successives :
#   1. npm 10 (fourni avec Node 22), lockfile existant ;
#   2. npm 10, lockfile reconstruit depuis package.json ;
#   3-4. npm 11 (anomalie corrigée dans les versions récentes d'Arborist), lockfile existant puis reconstruit.
# Le lockfile retenu doit ensuite être accepté par « npm ci » de npm 10 (celui de l'image Docker).
attempt() { # $1 = libellé ; $2 = commande npm ; $3 = "frais" pour repartir sans lockfile
  fresh_copy /tmp/web
  [ "${3:-}" = "frais" ] && rm -f /tmp/web/package-lock.json
  # npm n'applique pas un « override » à une entrée déjà verrouillée : on retire du lockfile copié
  # le postcss figé par Next.js pour qu'il soit résolu à nouveau selon l'override.
  [ -f /tmp/web/package-lock.json ] && node -e '
    const fs=require("fs");const f="/tmp/web/package-lock.json";const d=JSON.parse(fs.readFileSync(f,"utf8"));
    delete d.packages["node_modules/next/node_modules/postcss"];fs.writeFileSync(f,JSON.stringify(d,null,2)+"\n");'
  if (cd /tmp/web && $2 install --package-lock-only $FLAGS > /tmp/npm.out 2>&1) \
     && (cd /tmp/web && npm ci --dry-run $FLAGS > /dev/null 2>&1) \
     && check_postcss; then
    cp /tmp/web/package-lock.json "$SRC/package-lock.json"
    echo "lock-ok ($1)"
    exit 0
  fi
  echo "tentative « $1 » : ÉCHEC : $(grep -m1 'npm error' /tmp/npm.out)"
}
attempt "npm 10, lockfile existant" "npm"
attempt "npm 10, lockfile reconstruit" "npm" frais
attempt "npm 11, lockfile existant" "npx -y npm@11"
attempt "npm 11, lockfile reconstruit" "npx -y npm@11" frais
echo "Aucune tentative n'a abouti. Fin du journal npm :"; log_tail

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

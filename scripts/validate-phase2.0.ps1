# =============================================================================
# NurseLearn AI — Validation de la Phase 2.0 (sécurité et qualité)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.0-security-quality) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.0.ps1
#
# 1. met à jour package-lock.json selon package.json (sans installer dans votre dossier) ;
# 2. audits de dépendances : npm audit (rapport JSON) et pip-audit ;
# 3. qualité : Ruff (lint + format), mypy, ESLint, Prettier, TypeScript ;
# 4. non-régression complète : scripts\validate-phase1.2.ps1 (pile Docker, API, Web, pytest,
#    Vitest, Playwright + axe, dont les nouveaux tests de sécurité HTTP) ;
# 5. contrôles de sécurité HTTP propres à la Phase 2.0 sur la pile démarrée.
# Ne supprime aucun volume ni aucune donnée ; journal : docs\validation-logs\phase2.0-<date>.log
# =============================================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$root = (Get-Location).Path
if (-not (Test-Path "$root\compose.yaml")) { Write-Error "Lancer depuis la racine du dépôt (C:\NurseLearnAI-git)."; exit 2 }
if (-not (Test-Path "$root\.env")) { Write-Error ".env absent."; exit 2 }
$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "phase2.0-$stamp.log"
$results = [ordered]@{}

function Log([string]$msg) { Write-Host $msg; Add-Content -Path $script:log -Value $msg -Encoding UTF8 }
function Run([string]$title, [scriptblock]$cmd) {
    Log ""; Log ">>> $title"
    $out = & $cmd 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    foreach ($l in $out) { Log "    $l" }
    Log "    [code retour : $code]"
    return @{ Code = $code; Out = ($out -join "`n") }
}
function Result([string]$name, [bool]$ok, [string]$detail = "") {
    $script:results[$name] = $ok
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    Log "    => [$tag] $name $detail"
}
function Head([string]$url, [hashtable]$headers = @{}) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Uri $url -Headers $headers -MaximumRedirection 0
        return @{ Status = [int]$r.StatusCode; Headers = $r.Headers; Body = $r.Content }
    } catch {
        $st = 0; $h = @{}
        if ($_.Exception.Response) { $st = [int]$_.Exception.Response.StatusCode; $h = $_.Exception.Response.Headers }
        return @{ Status = $st; Headers = $h; Body = "" }
    }
}

$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envVars[$k.Trim()] = $v.Trim() }
$apiPort = if ($envVars['API_PORT']) { $envVars['API_PORT'] } else { "28000" }
$webPort = if ($envVars['WEB_PORT']) { $envVars['WEB_PORT'] } else { "3000" }
$api = "http://localhost:$apiPort"; $web = "http://localhost:$webPort"

Log "NurseLearn AI — validation Phase 2.0 — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_0" ($branch -eq "phase-2.0-security-quality") $branch

# -----------------------------------------------------------------------------
# 1. package-lock.json aligné sur package.json (aucun node_modules écrit chez vous)
# -----------------------------------------------------------------------------
# Travail sur une copie dans le conteneur : le lockfile du dépôt n'est remplacé qu'en cas de succès ;
# en cas d'échec, diagnostic automatique (variantes de package.json, fin du journal npm).
$r = Run "Mise à jour de package-lock.json (scripts/npm-lock.sh)" {
    docker run --rm -v "${root}:/repo" node:22-bookworm sh /repo/scripts/npm-lock.sh
}
Result "lock_npm_coherent" ($r.Code -eq 0 -and $r.Out -match 'lock-ok')
if (-not $results["lock_npm_coherent"]) {
    # Sans lockfile cohérent, la construction Docker (npm ci) échouerait : inutile de continuer 15 minutes.
    Log ""; Log "ARRÊT : package-lock.json n'a pas pu être mis à jour (diagnostic ci-dessus). Le lockfile du dépôt est intact."
    Log "Journal : $log"
    exit 1
}

# -----------------------------------------------------------------------------
# 2. Audits de dépendances
# -----------------------------------------------------------------------------
$r = Run "npm audit (rapport JSON complet et production seule)" {
    docker run --rm -v "${root}:/repo" -w /repo/apps/web node:22-bookworm sh -c "npm audit --package-lock-only --json > /repo/docs/validation-logs/npm-audit-$stamp.json; npm audit --package-lock-only --omit=dev --json > /repo/docs/validation-logs/npm-audit-prod-$stamp.json; npm audit --package-lock-only --audit-level=high; echo audit-code=`$?"
}
$audit = $null
try { $audit = Get-Content "$logDir\npm-audit-$stamp.json" -Raw | ConvertFrom-Json } catch { }
$v = if ($audit) { $audit.metadata.vulnerabilities } else { $null }
$detail = if ($v) { "critiques=$($v.critical) élevées=$($v.high) modérées=$($v.moderate) faibles=$($v.low)" } else { "rapport illisible" }
Result "npm_aucune_vulnerabilite_elevee_ou_critique" ($v -and $v.critical -eq 0 -and $v.high -eq 0) $detail

$r = Run "pip-audit (dépendances de l'API)" {
    docker run --rm -v "${root}:/repo" -w /repo/services/api -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c "pip install -q --root-user-action=ignore -e '.[dev]' pip-audit && pip freeze --exclude-editable > /tmp/req.txt && pip-audit --strict --disable-pip --no-deps -r /tmp/req.txt"
}
Result "pip_audit_sans_vulnerabilite" ($r.Code -eq 0)

# -----------------------------------------------------------------------------
# 3. Qualité du code
# -----------------------------------------------------------------------------
$r = Run "Python : Ruff (lint + format) et mypy" {
    docker run --rm -v "${root}:/repo" -w /repo/services/api -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c "pip install -q --root-user-action=ignore -e '.[dev]' && cd /repo && ruff check . && ruff format --check . && cd services/api && mypy"
}
Result "python_ruff_mypy" ($r.Code -eq 0)
$r = Run "Web : ESLint, Prettier, TypeScript" {
    docker run --rm -v "${root}:/repo" -v /repo/apps/web/node_modules -w /repo/apps/web node:22-bookworm sh -c "npm ci --no-audit --no-fund --loglevel=error && npm run lint && npm run format:check && npm run typecheck"
}
Result "web_eslint_prettier_typescript" ($r.Code -eq 0)

# -----------------------------------------------------------------------------
# 4. Non-régression complète (Phase 1.2 : pile Docker, API, Web, tests, Playwright + axe)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase1.2.ps1"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase1.2.ps1" | Out-Null
# Seul un journal créé par CETTE exécution compte (sinon le script n'a pas pu démarrer).
$reg = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_phase_1_2" $false "aucun nouveau journal : validate-phase1.2.ps1 n'a pas démarré"
    $reg = $null
}
if ($reg) {
$summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles, (\d+) échec" | Select-Object -Last 1
$fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
Result "non_regression_phase_1_2" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
}

# -----------------------------------------------------------------------------
# 5. Sécurité HTTP (pile démarrée par l'étape 4)
# -----------------------------------------------------------------------------
$a = Head "$api/api/v1/countries"
Result "api_xfo_csp" ("$($a.Headers['X-Frame-Options'])" -eq "DENY" -and "$($a.Headers['Content-Security-Policy'])" -match "default-src 'none'")
Result "api_sans_entete_server" (-not "$($a.Headers['Server'])") "$($a.Headers['Server'])"
Result "api_entetes_ratelimit" ("$($a.Headers['RateLimit-Limit'])" -ne "")  "limite=$($a.Headers['RateLimit-Limit']) restant=$($a.Headers['RateLimit-Remaining'])"
# X-Forwarded-For d'un client direct (non approuvé) est ignoré : le compteur restant diminue quand même.
$r1 = Head "$api/api/v1/countries" @{ "X-Forwarded-For" = "198.51.100.1" }
$r2 = Head "$api/api/v1/countries" @{ "X-Forwarded-For" = "198.51.100.2" }
Result "api_xff_non_approuve_ignore" ([int]"$($r2.Headers['RateLimit-Remaining'])" -lt [int]"$($r1.Headers['RateLimit-Remaining'])") "restant $($r1.Headers['RateLimit-Remaining']) puis $($r2.Headers['RateLimit-Remaining'])"
$x = Head "$api/api/v1/inexistant"
Result "api_404_problem_json" ($x.Status -eq 404)
$w = Head "$web/fr/pays"
$csp = "$($w.Headers['Content-Security-Policy'])"
Result "web_csp_nonce" ($csp -match "'nonce-[A-Za-z0-9+/=]+'" -and $csp -match "frame-ancestors 'none'" -and $csp -notmatch "unsafe-eval") $csp
Result "web_x_frame_options" ("$($w.Headers['X-Frame-Options'])" -eq "DENY")
Result "web_sans_x_powered_by" (-not "$($w.Headers['X-Powered-By'])")
foreach ($u in "/fr/inexistant", "/fr/metiers?j=XX-ZZ", "/fr/programmes/00000000-0000-0000-0000-000000000000") {
    $n = Head "$web$u"
    Result ("web_404_reel_" + ($u -replace '[^a-z0-9]', '_')) ($n.Status -eq 404) "HTTP $($n.Status)"
}

Log ""; Log "================ SYNTHÈSE PHASE 2.0 ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-48} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles Phase 2.0 (+ non-régression Phase 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
Log "Rapports npm audit : docs\validation-logs\npm-audit-$stamp.json et npm-audit-prod-$stamp.json"
if ($fail -gt 0) { exit 1 } else { exit 0 }

# =============================================================================
# NurseLearn AI — Validation du lot 1.2 (API FastAPI + interface web)
# Usage (PowerShell, à la racine du dépôt) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-lot1.2.ps1
#
# - construit et démarre les services « app » (api, web) en plus de l'infrastructure ;
# - vérifie l'API et les pages web par HTTP ;
# - exécute les tests d'API (pytest) sur une base jetable « nl_validation_api » ;
# - exécute le parcours e2e Playwright + axe dans un conteneur Node (node_modules isolé dans un volume
#   anonyme : rien de spécifique à Linux n'est écrit dans votre dossier, hormis package-lock.json) ;
# - ne supprime aucun volume ni aucune donnée existante ;
# - journal : docs\validation-logs\lot1.2-<date>.log (secrets masqués).
# =============================================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$root = (Get-Location).Path
if (-not (Test-Path "$root\infra\docker-compose.yml")) { Write-Error "Lancer depuis la racine du dépôt."; exit 2 }
if (-not (Test-Path "$root\.env")) { Write-Error ".env absent : lancez d'abord scripts\validate-phase1.1.ps1"; exit 2 }

$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("lot1.2-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
$results = [ordered]@{}
$secrets = @()

function Mask([string]$text) { foreach ($s in $script:secrets) { if ($s -and $s.Length -ge 6) { $text = $text.Replace($s, "***") } }; return $text }
function Log([string]$msg) { $line = Mask $msg; Write-Host $line; Add-Content -Path $script:log -Value $line -Encoding UTF8 }
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
function Http([string]$url, [hashtable]$headers = @{}) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $url -Headers $headers -MaximumRedirection 5
        return @{ Status = [int]$r.StatusCode; Body = $r.Content; Headers = $r.Headers }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return @{ Status = $status; Body = "$($_.Exception.Message)"; Headers = @{} }
    }
}

$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envVars[$k.Trim()] = $v.Trim() }
foreach ($k in $envVars.Keys) {
    if ($k -match 'PASSWORD|SECRET|SIGNING|ENCRYPTION|API_KEY|TOKEN' -or $k -in @('DATABASE_URL','REDIS_URL')) { if ($envVars[$k]) { $script:secrets += $envVars[$k] } }
}
$PGU = $envVars['POSTGRES_USER']; $PGPW = $envVars['POSTGRES_PASSWORD']
$compose = @("compose", "-f", "infra/docker-compose.yml", "--env-file", ".env", "--profile", "app")
$api = "http://localhost:8000"; $web = "http://localhost:3000"

Log "NurseLearn AI — validation lot 1.2 — $(Get-Date -Format o)"
Log "Machine : $env:COMPUTERNAME — dossier $root — Git : $(git branch --show-current 2>$null) @ $(git rev-parse --short HEAD 2>$null)"

# -----------------------------------------------------------------------------
# 1. Construction et démarrage
# -----------------------------------------------------------------------------
$r = Run "Construction des images api et web (plusieurs minutes la première fois)" { docker @compose build api web }
Result "images_construites" ($r.Code -eq 0)
$r = Run "Démarrage (infrastructure + application)" { docker @compose up -d }
Result "services_demarres" ($r.Code -eq 0)

Log "Attente de l'API (max 180 s)..."
$deadline = (Get-Date).AddSeconds(180)
do { Start-Sleep -Seconds 3; $h = docker inspect -f "{{.State.Health.Status}}" (docker @compose ps -q api) 2>$null } while ($h -ne "healthy" -and (Get-Date) -lt $deadline)
Result "api_healthy" ($h -eq "healthy") $h
if ($h -ne "healthy") { Run "Journal de l'API" { docker @compose logs --tail 80 api } | Out-Null }
$deadline = (Get-Date).AddSeconds(90)
do { Start-Sleep -Seconds 3; $w = Http "$web/fr" } while ($w.Status -ne 200 -and (Get-Date) -lt $deadline)
Result "web_repond" ($w.Status -eq 200) "HTTP $($w.Status)"

# -----------------------------------------------------------------------------
# 2. Contrôles HTTP de l'API
# -----------------------------------------------------------------------------
$x = Http "$api/ready";                                Result "api_ready" ($x.Status -eq 200 -and $x.Body -match '"database":"ok"' -and $x.Body -match '"redis":"ok"') $x.Body
$x = Http "$api/api/v1/jurisdictions";                 Result "api_juridictions" ($x.Status -eq 200 -and $x.Body -match 'CA-QC')
$x = Http "$api/api/v1/jurisdictions/CA-QC/professions"; Result "api_metiers_qc" ($x.Status -eq 200 -and $x.Body -match '"code":"pab"' -and $x.Body -match 'pending_expert_review')
$x = Http "$api/api/v1/jurisdictions/CA-QC/professions" @{ "Accept-Language" = "en" }; Result "api_anglais" ($x.Body -match 'Patient attendant')
$x = Http "$api/api/v1/jurisdictions/XX-ZZ/professions"; Result "api_404_problem_json" ($x.Status -eq 404)
$x = Http "$api/api/v1/specialties";                   Result "api_specialites" ($x.Status -eq 200 -and ([regex]::Matches($x.Body, '"code"')).Count -eq 27)
$x = Http "$api/api/v1/programs?jurisdiction=CA-QC&profession=pab"; Result "api_pilote_non_publie" ($x.Status -eq 200 -and $x.Body.Trim() -eq "[]") $x.Body
$x = Http "$api/api/v1/modules/hygiene-mains-prevention-infections"; Result "api_module_brouillon_cache" ($x.Status -eq 404)
$x = Http "$api/openapi.json";                         Result "api_openapi" ($x.Status -eq 200)

# -----------------------------------------------------------------------------
# 3. Contrôles HTTP de l'interface web
# -----------------------------------------------------------------------------
$x = Http "$web/";                                     Result "web_redirection_langue" ($x.Status -eq 200)
$x = Http "$web/fr";                                   Result "web_accueil" ($x.Body -match 'ni un diplôme')
$x = Http "$web/fr/pays";                              Result "web_pays" ($x.Body -match 'Québec')
$x = Http "$web/fr/metier?j=CA-QC";                    Result "web_metier" ($x.Body -match 'Préposé' -and $x.Body -match 'en cours de validation')
$x = Http "$web/fr/catalogue?j=CA-QC&p=pab";           Result "web_catalogue_vide" ($x.Body -match 'Contenu en préparation' -and $x.Body -notmatch 'Hygiène des mains')
$x = Http "$web/en/metier?j=CA-QC";                    Result "web_anglais" ($x.Body -match 'Patient attendant')

# -----------------------------------------------------------------------------
# 4. Tests d'API (pytest) sur une base jetable
# -----------------------------------------------------------------------------
docker @compose exec -T postgres psql -U $PGU -d postgres -c "DROP DATABASE IF EXISTS nl_validation_api WITH (FORCE)" | Out-Null
docker @compose exec -T postgres psql -U $PGU -d postgres -c "CREATE DATABASE nl_validation_api" | Out-Null
$r = Run "pytest API (base nl_validation_api)" {
    docker run --rm --network nurselearn_default -v "${root}:/repo" -w /repo/services/api `
        -e "DATABASE_URL=postgresql+asyncpg://${PGU}:${PGPW}@postgres:5432/nl_validation_api" `
        -e "REDIS_URL=redis://:$($envVars['REDIS_PASSWORD'])@redis:6379/0" -e PYTHONDONTWRITEBYTECODE=1 `
        python:3.12-slim sh -c "pip install -q --root-user-action=ignore -e '.[dev]' && alembic upgrade head && python -m app.seed && python -m pytest -q -rs -p no:cacheprovider tests/api"
}
Result "pytest_api" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "passed|failed|error") -join " ")
docker @compose exec -T postgres psql -U $PGU -d postgres -c "DROP DATABASE IF EXISTS nl_validation_api WITH (FORCE)" | Out-Null

# -----------------------------------------------------------------------------
# 5. Parcours e2e Playwright + axe (dans un conteneur Node, contre le service web)
# -----------------------------------------------------------------------------
$r = Run "Playwright e2e + axe" {
    docker run --rm --network nurselearn_default -v "${root}:/repo" -v /repo/apps/web/node_modules -w /repo/apps/web -e E2E_BASE_URL=http://web:3000 -e CI=1 `
        node:22-bookworm sh -c "npm install --no-audit --no-fund --silent && npx playwright install --with-deps chromium > /dev/null && npx playwright test --reporter=list"
}
Result "e2e_axe" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "passed|failed") -join " ")

# -----------------------------------------------------------------------------
# 6. Secrets dans les journaux
# -----------------------------------------------------------------------------
$logs = (docker @compose logs --no-color 2>&1 | Out-String)
$leaks = 0
foreach ($k in 'POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SIGNING_KEY') { $v = $envVars[$k]; if ($v -and $logs.Contains($v)) { $leaks++ } }
Result "aucun_secret_dans_les_journaux" (($leaks -eq 0) -and ($logs.Length -gt 1024)) "($leaks fuite(s), $([int]($logs.Length/1024)) Ko)"

Log ""; Log "================ SYNTHÈSE ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-34} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles, {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
Log "Application : $web/fr  ·  API : $api/docs"
Log "Arrêt sans perte de données : docker compose -f infra/docker-compose.yml --env-file .env --profile app stop"
if ($fail -gt 0) { exit 1 } else { exit 0 }

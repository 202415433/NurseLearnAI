# =============================================================================
# NurseLearn AI — Validation de la Phase 1.2 (API FastAPI + parcours web)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase1.2.ps1
#
# - démarre TOUTE la pile avec la commande cible :  docker compose up --build -d
#   (PostgreSQL, Redis, RustFS et Mailpit sont réutilisés tels quels, volumes conservés) ;
# - vérifie l'API, le Web, la communication entre services, CORS, ports Windows, persistance ;
# - exécute pytest (base, migrations, API, unitaires) sur des bases jetables, Vitest et Playwright + axe ;
# - ne supprime aucun volume ni aucune donnée existante ; ne publie aucun contenu ;
# - journal : docs\validation-logs\phase1.2-<date>.log (secrets masqués).
# =============================================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$root = (Get-Location).Path
if (-not (Test-Path "$root\compose.yaml")) { Write-Error "Lancer depuis la racine du dépôt (C:\NurseLearnAI-git)."; exit 2 }
if (-not (Test-Path "$root\.env")) { Write-Error ".env absent : copiez .env.example vers .env et remplacez les valeurs change-me."; exit 2 }

$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("phase1.2-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
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
function Http([string]$url, [hashtable]$headers = @{}, [string]$method = "GET") {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Uri $url -Headers $headers -Method $method -MaximumRedirection 5
        return @{ Status = [int]$r.StatusCode; Body = $r.Content; Headers = $r.Headers }
    } catch {
        $status = 0; $body = "$($_.Exception.Message)"; $h = @{}
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            $h = $_.Exception.Response.Headers
            try { $body = (New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())).ReadToEnd() } catch { }
        }
        return @{ Status = $status; Body = $body; Headers = $h }
    }
}
function Json([string]$url, [hashtable]$headers = @{}) {
    $x = Http $url $headers
    if ($x.Status -ne 200) { return $null }
    try { return ($x.Body | ConvertFrom-Json) } catch { return $null }
}

$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envVars[$k.Trim()] = $v.Trim() }
foreach ($k in $envVars.Keys) {
    if ($k -match 'PASSWORD|SECRET|SIGNING|ENCRYPTION|API_KEY|TOKEN' -or $k -in @('DATABASE_URL','REDIS_URL')) { if ($envVars[$k]) { $script:secrets += $envVars[$k] } }
}
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
$PGPW = $envVars['POSTGRES_PASSWORD']
$apiPort = if ($envVars['API_PORT']) { $envVars['API_PORT'] } else { "18000" }
$webPort = if ($envVars['WEB_PORT']) { $envVars['WEB_PORT'] } else { "3000" }
$pgPort  = if ($envVars['POSTGRES_PORT']) { $envVars['POSTGRES_PORT'] } else { "55432" }
$api = "http://localhost:$apiPort"; $web = "http://localhost:$webPort"
$net = "nurselearn_default"
function Psql([string]$db, [string]$sql) { docker compose exec -T postgres psql -U $PGU -d $db -v ON_ERROR_STOP=1 -X -q -At -c $sql 2>&1 }

Log "NurseLearn AI — validation Phase 1.2 — $(Get-Date -Format o)"
Log "Machine : $env:COMPUTERNAME — dossier $root — Git : $(git branch --show-current 2>$null) @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_1_2" ((git branch --show-current 2>$null) -eq "phase-1.2") "$(git branch --show-current 2>$null)"

# -----------------------------------------------------------------------------
# 1. Démarrage avec la commande cible
# -----------------------------------------------------------------------------
$r = Run "Version de Docker Compose (2.20+ requis pour compose.yaml à la racine)" { docker compose version --short }
$r = Run "Configuration Compose (racine)" { docker compose config --services }
Result "compose_six_services" ($r.Code -eq 0 -and $r.Out -match 'postgres' -and $r.Out -match 'redis' -and $r.Out -match 's3' -and $r.Out -match 'mailpit' -and $r.Out -match 'api' -and $r.Out -match 'web')
# Ports : un autre programme Windows qui écoute déjà sur le port de l'API ou du Web empêche leur démarrage
# (exécution du 18/11 : 127.0.0.1:8000 était occupé par une autre application, d'où le port 18000 par défaut).
foreach ($pair in @(@('api', $apiPort), @('web', $webPort))) {
    $svc = $pair[0]; $port = [int]$pair[1]
    $owners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
    $holders = @()
    foreach ($procId in $owners) { $holders += "$((Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName) (PID $procId)" }
    # Conteneurs (de n'importe quel projet) qui publient déjà ce port
    $pub = docker ps --format "{{.Names}} {{.Ports}}" 2>$null | Where-Object { $_ -match ":$port->" }
    $ours = $pub | Where-Object { $_ -match "^nurselearn-$svc-1 " }
    $busy = ($holders.Count -gt 0) -and -not $ours
    $detail = if ($busy) { "occupé par $($holders -join ', ')$(if ($pub) { ' — conteneur : ' + ($pub -join '; ') }) : libérez-le ou choisissez un autre port dans .env (API_PORT / WEB_PORT). Détails : scripts\diagnose-ports.ps1" } else { "" }
    Result "port_${svc}_${port}_libre" (-not $busy) $detail
}

# Conteneurs api/web d'une tentative précédente : sans données, ils sont recréés pour repartir d'un état
# réseau sain (une tentative interrompue par un conflit de port peut laisser un conteneur sans réseau).
# Les volumes (pgdata, redisdata, s3data) ne sont jamais touchés.
$r = Run "Retrait des anciens conteneurs api et web (sans données)" { docker compose rm -s -f api web }

$r = Run "docker compose up --build -d (plusieurs minutes la première fois)" { docker compose up --build -d }
Result "compose_up_build" ($r.Code -eq 0)
if ($r.Code -ne 0) { Run "Diagnostic des ports (lecture seule)" { powershell -ExecutionPolicy Bypass -File "$root\scripts\diagnose-ports.ps1" } | Out-Null }

Log "Attente de la santé des services (max 240 s)..."
$deadline = (Get-Date).AddSeconds(240)
do {
    Start-Sleep -Seconds 4
    $states = @{}
    foreach ($s in 'postgres','redis','s3','api','web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
    $allOk = -not ($states.Values | Where-Object { $_ -ne "healthy" })
} while (-not $allOk -and (Get-Date) -lt $deadline)
foreach ($s in 'postgres','redis','s3','api','web') { Result "sante_$s" ($states[$s] -eq "healthy") $states[$s] }
$m = docker inspect -f "{{.State.Status}}" (docker compose ps -q mailpit) 2>$null
Result "mailpit_en_marche" ($m -eq "running") $m
if (-not $allOk) { Run "Journaux api/web" { docker compose logs --tail 60 api web } | Out-Null }
$before = (Psql $PGDB "SELECT count(*) FROM professions") | Select-Object -First 1
Log "    professions en base : $before"

# -----------------------------------------------------------------------------
# 2. API : santé, données publiques, filtres, validation, sécurité
# -----------------------------------------------------------------------------
$x = Http "$api/health";  Result "api_health" ($x.Status -eq 200)
$x = Http "$api/ready";   Result "api_ready_pg_redis" ($x.Status -eq 200 -and $x.Body -match '"database":"ok"' -and $x.Body -match '"redis":"ok"') $x.Body
$x = Http "$api/openapi.json"; Result "api_openapi" ($x.Status -eq 200 -and $x.Body -match '/api/v1/programs/\{program_id\}')

$c = Json "$api/api/v1/countries"
Result "api_pays" ($c -and (($c.items.code -join ",") -match 'CA') -and (($c.items.code -join ",") -match 'FR') -and (($c.items.code -join ",") -match 'SN')) (($c.items.code) -join ",")
$j = Json "$api/api/v1/jurisdictions?country=CA"
Result "api_juridictions_canada" ($j -and $j.total -eq 5 -and (($j.items.code) -contains 'CA-QC') -and (($j.items.code) -contains 'CA-ON')) "total=$($j.total)"
$p = Json "$api/api/v1/professions?jurisdiction=CA-QC"
$pab = $p.items | Where-Object { $_.code -eq 'pab' }
Result "api_metiers_quebec" ($pab -and $pab.validation_status -eq 'pending_expert_review') ""
Result "api_pilote_en_preparation" ($pab -and $pab.availability.published -eq 0 -and $pab.availability.in_preparation -eq 1) "published=$($pab.availability.published) in_preparation=$($pab.availability.in_preparation)"
Result "api_aucune_equivalence" ($p -and -not (($p.items.code) -contains 'psw') -and -not (($p.items.code) -contains 'aide_soignant'))
$on = Json "$api/api/v1/professions?jurisdiction=CA-ON"; Result "api_metiers_ontario" ($on -and (($on.items.code) -contains 'psw'))
$fr = Json "$api/api/v1/professions?jurisdiction=FR";    Result "api_metiers_france" ($fr -and (($fr.items.code) -contains 'aide_soignant'))
$sn = Json "$api/api/v1/professions?jurisdiction=SN";    Result "api_metiers_senegal" ($sn -and (($sn.items.code) -contains 'infirmier_etat'))
$x = Http "$api/api/v1/professions?jurisdiction=CA-QC" @{ "Accept-Language" = "en" }; Result "api_anglais" ($x.Body -match 'Patient attendant')
$s = Json "$api/api/v1/specialties"; Result "api_specialites" ($s -and $s.total -eq 27) "total=$($s.total)"
$g = Json "$api/api/v1/programs?jurisdiction=CA-QC&profession=pab"; Result "api_brouillon_invisible_liste" ($g -and $g.total -eq 0) "total=$($g.total)"
$pilotId = (Psql $PGDB "SELECT id FROM programs WHERE code='pab-qc-socle'") | Select-Object -First 1
$x = Http "$api/api/v1/programs/$pilotId"; Result "api_brouillon_invisible_detail" ($x.Status -eq 404 -and $x.Body -notmatch 'Hygi') "HTTP $($x.Status)"
$x = Http "$api/api/v1/professions?jurisdiction=%3Cscript%3E"
Result "api_validation_422_problem" ($x.Status -eq 422 -and "$($x.Headers['Content-Type'])" -match 'problem\+json') "HTTP $($x.Status)"
$x = Http "$api/api/v1/programs?limit=1000"; Result "api_pagination_bornee" ($x.Status -eq 422)
$x = Http "$api/api/v1/countries" @{} "POST"; Result "api_lecture_seule_405" ($x.Status -eq 405) "HTTP $($x.Status)"
$x = Http "$api/api/v1/countries"
Result "api_entetes_securite" ("$($x.Headers['X-Content-Type-Options'])" -eq 'nosniff' -and "$($x.Headers['Cache-Control'])" -match 'no-store')
$leak = $false; foreach ($v in $secrets) { if ($v.Length -ge 6 -and ($x.Body + (Http "$api/ready").Body + (Http "$api/openapi.json").Body).Contains($v)) { $leak = $true } }
Result "api_aucun_secret_dans_reponses" (-not $leak)
$dbCount = (Psql $PGDB "SELECT count(*) FROM jurisdictions WHERE is_active") | Select-Object -First 1
$all = Json "$api/api/v1/jurisdictions"
Result "donnees_issues_de_postgresql" ($all -and "$($all.total)" -eq "$dbCount") "API=$($all.total) PostgreSQL=$dbCount"

# CORS : origine du Web autorisée, origine inconnue refusée
$ok = Http "$api/api/v1/countries" @{ "Origin" = "http://localhost:3000" }
$evil = Http "$api/api/v1/countries" @{ "Origin" = "https://evil.example" }
Result "cors_origine_web_autorisee" ("$($ok.Headers['Access-Control-Allow-Origin'])" -eq 'http://localhost:3000')
Result "cors_origine_inconnue_refusee" (-not "$($evil.Headers['Access-Control-Allow-Origin'])")

# -----------------------------------------------------------------------------
# 3. Communication entre services et ports Windows
# -----------------------------------------------------------------------------
$r = Run "Le conteneur web joint l'API par le réseau Docker" { docker compose exec -T web node -e "fetch('http://api:8000/ready').then(r=>{console.log(r.status);process.exit(r.ok?0:1)}).catch(e=>{console.log(e.message);process.exit(1)})" }
Result "web_vers_api_interne" ($r.Code -eq 0)
$listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @([int]$apiPort, [int]$webPort, [int]$pgPort) }
Log "    Ports à l'écoute : $(($listen | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort)" }) -join ', ')"
Result "ports_windows_ouverts" ((($listen.LocalPort | Sort-Object -Unique) -join ",") -eq ((@([int]$apiPort, [int]$webPort, [int]$pgPort) | Sort-Object) -join ","))
Result "ports_limites_a_localhost" (-not ($listen | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') }))

# -----------------------------------------------------------------------------
# 4. Interface web (rendu serveur, données de l'API)
# -----------------------------------------------------------------------------
$x = Http "$web/";                                 Result "web_redirection_langue" ($x.Status -eq 200)
$x = Http "$web/fr";                               Result "web_accueil" ($x.Body -match 'ni un diplôme')
$x = Http "$web/fr/pays";                          Result "web_pays" ($x.Body -match 'Canada' -and $x.Body -match 'Sénégal' -and $x.Body -match 'France')
$x = Http "$web/fr/juridictions?pays=CA";          Result "web_juridictions" ($x.Body -match 'Québec' -and $x.Body -match 'Ontario')
$x = Http "$web/fr/metiers?j=CA-QC";               Result "web_metiers" ($x.Body -match 'Préposé' -and $x.Body -match 'non encore validé')
$x = Http "$web/fr/specialites?j=CA-QC&p=pab";     Result "web_specialites" ($x.Status -eq 200 -and $x.Body -match 'Catalogue des spécialités')
$x = Http "$web/fr/programmes?j=CA-QC&p=pab";      Result "web_programmes_brouillon_invisible" ($x.Body -match 'Aucun programme disponible' -and $x.Body -notmatch 'Hygiène des mains')
$x = Http "$web/fr/programmes/$pilotId";           Result "web_detail_brouillon_introuvable" ($x.Body -match 'Page introuvable' -and $x.Body -notmatch 'Hygiène des mains')
$x = Http "$web/en/metiers?j=CA-QC";               Result "web_anglais" ($x.Body -match 'Patient attendant')
$x = Http "$web/fr/metiers?j=XX-ZZ";               Result "web_juridiction_inconnue" ($x.Body -match 'Page introuvable')

# -----------------------------------------------------------------------------
# 5. Persistance : redémarrage de PostgreSQL sans perte
# -----------------------------------------------------------------------------
$r = Run "Redémarrage de PostgreSQL" { docker compose restart postgres }
$deadline = (Get-Date).AddSeconds(90)
do { Start-Sleep -Seconds 3; $ready = Http "$api/ready" } while ($ready.Status -ne 200 -and (Get-Date) -lt $deadline)
$after = (Psql $PGDB "SELECT count(*) FROM professions") | Select-Object -First 1
Result "persistance_apres_redemarrage" ($ready.Status -eq 200 -and "$after" -eq "$before" -and "$after" -ne "") "avant=$before après=$after"
$rev = Psql $PGDB "SELECT version_num FROM alembic_version"
Result "migrations_a_jour" ("$rev" -match '0003') "$rev"

# -----------------------------------------------------------------------------
# 6. Tests automatisés
# -----------------------------------------------------------------------------
Psql "postgres" "DROP DATABASE IF EXISTS nl_validation_api WITH (FORCE)" | Out-Null
Psql "postgres" "CREATE DATABASE nl_validation_api" | Out-Null
$r = Run "pytest : base, migrations, API, unitaires (bases jetables)" {
    docker run --rm --network $net -v "${root}:/repo" -w /repo `
        -e "DATABASE_URL=postgresql+asyncpg://${PGU}:${PGPW}@postgres:5432/nl_validation_api" `
        -e "REDIS_URL=redis://:$($envVars['REDIS_PASSWORD'])@redis:6379/0" `
        -e DEBIAN_FRONTEND=noninteractive -e NL_TEST_PGHOST=postgres -e NL_TEST_PGPORT=5432 -e NL_TEST_PGUSER=$PGU -e PGPASSWORD=$PGPW -e PYTHONDONTWRITEBYTECODE=1 `
        python:3.12-slim sh -c "apt-get update -qq >/dev/null && apt-get install -y -qq postgresql-client >/dev/null && cd services/api && pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]' && alembic upgrade head && python -m app.seed && cd /repo && python -m pytest -q -rs -p no:cacheprovider database/tests services/api/tests"
}
Result "pytest_complet" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "passed|failed|error") -join " ")
Psql "postgres" "DROP DATABASE IF EXISTS nl_validation_api WITH (FORCE)" | Out-Null

$r = Run "Web : typecheck + tests de composants (Vitest) + parcours Playwright + axe" {
    docker run --rm --network $net -v "${root}:/repo" -v /repo/apps/web/node_modules -w /repo/apps/web -e E2E_BASE_URL=http://web:3000 -e CI=1 `
        node:22-bookworm sh -c "npm install --no-audit --no-fund --silent && npm run typecheck && npm test && npx playwright install --with-deps chromium > /dev/null && npx playwright test --reporter=list"
}
Result "web_tests_vitest_playwright_axe" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "Tests |passed|failed|skipped") -join " ")

# -----------------------------------------------------------------------------
# 7. Secrets dans les journaux
# -----------------------------------------------------------------------------
$logs = (docker compose logs --no-color 2>&1 | Out-String)
$leaks = 0
foreach ($k in 'POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SIGNING_KEY') { $v = $envVars[$k]; if ($v -and $logs.Contains($v)) { $leaks++ } }
Result "aucun_secret_dans_les_journaux" (($leaks -eq 0) -and ($logs.Length -gt 1024)) "($leaks fuite(s), $([int]($logs.Length/1024)) Ko)"

Log ""; Log "================ SYNTHÈSE ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-38} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles, {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
Log "Application : $web/fr  ·  API : $api/docs"
Log "Arrêt sans perte de données : docker compose stop"
if ($fail -gt 0) { exit 1 } else { exit 0 }

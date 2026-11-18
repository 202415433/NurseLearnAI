# =============================================================================
# NurseLearn AI — Validation Phase 1.1 (Docker, Alembic réel, pgvector, tests)
# Usage (PowerShell, à la racine du dépôt) :  .\scripts\validate-phase1.1.ps1
#
# Ce script :
#   - ne supprime AUCUN volume ni aucune base existante ; il crée seulement des
#     bases jetables préfixées "nl_validation_" qu'il supprime ensuite ;
#   - crée .env à partir de .env.example avec des secrets aléatoires si .env
#     n'existe pas (un .env existant n'est jamais modifié) ;
#   - n'affiche jamais la valeur d'un secret ;
#   - écrit son journal dans docs\validation-logs\phase1.1-<date>.log
# =============================================================================
$ErrorActionPreference = "Continue"
# Sortie des outils (node, docker) lue en UTF-8 : évite les accents illisibles dans le journal.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$root = (Get-Location).Path
if (-not (Test-Path "$root\infra\docker-compose.yml")) { Write-Error "Lancer depuis la racine du dépôt."; exit 2 }

$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("phase1.1-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
$results = [ordered]@{}
$secrets = @()

function Mask([string]$text) {
    foreach ($s in $script:secrets) { if ($s -and $s.Length -ge 6) { $text = $text.Replace($s, "***") } }
    return $text
}
function Log([string]$msg) {
    $line = Mask $msg
    Write-Host $line
    Add-Content -Path $script:log -Value $line -Encoding UTF8
}
function Run([string]$title, [scriptblock]$cmd) {
    Log ""
    Log ">>> $title"
    $out = & $cmd 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    foreach ($l in $out) { Log "    $l" }
    Log "    [code retour : $code]"
    return @{ Code = $code; Out = ($out -join "`n") }
}
function Result([string]$name, [bool]$ok, [string]$detail = "") {
    $script:results[$name] = @{ Ok = $ok; Detail = $detail }
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    Log "    => [$tag] $name $detail"
}

Log "NurseLearn AI — validation Phase 1.1 — $(Get-Date -Format o)"
Log "Machine : $env:COMPUTERNAME — PowerShell $($PSVersionTable.PSVersion) — dossier $root"
$gitRev = (git rev-parse --short HEAD 2>$null); $gitBranch = (git branch --show-current 2>$null)
Log "Git : branche $gitBranch, commit $gitRev"

# -----------------------------------------------------------------------------
# 1. Docker Desktop
# -----------------------------------------------------------------------------
$r = Run "Docker : version client/serveur" { docker version --format "client {{.Client.Version}} / serveur {{.Server.Version}} ({{.Server.Os}})" }
Result "docker_desktop" ($r.Code -eq 0) $r.Out
if ($r.Code -ne 0) { Log "Docker Desktop ne répond pas : démarrez-le puis relancez le script."; exit 1 }
$r = Run "Docker Compose : version" { docker compose version }
Result "docker_compose" ($r.Code -eq 0)

# -----------------------------------------------------------------------------
# 2. Fichier .env (créé avec des secrets aléatoires s'il n'existe pas)
# -----------------------------------------------------------------------------
function NewSecret([int]$n = 32) {
    $bytes = New-Object byte[] $n
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', '').Substring(0, [Math]::Min(32, $n))
}
if (-not (Test-Path "$root\.env")) {
    $pg = NewSecret; $rd = NewSecret; $s3 = NewSecret; $jwt = NewSecret 64; $fk = NewSecret
    $lines = Get-Content "$root\.env.example" | ForEach-Object {
        switch -Regex ($_) {
            '^POSTGRES_PASSWORD=' { "POSTGRES_PASSWORD=$pg"; break }
            '^DATABASE_URL='      { $_.Replace("change-me", $pg); break }
            '^REDIS_PASSWORD='    { "REDIS_PASSWORD=$rd"; break }
            '^REDIS_URL='         { $_.Replace("change-me", $rd); break }
            '^S3_SECRET_KEY='     { "S3_SECRET_KEY=$s3"; break }
            '^JWT_SIGNING_KEY='   { "JWT_SIGNING_KEY=$jwt"; break }
            '^FIELD_ENCRYPTION_KEY=' { "FIELD_ENCRYPTION_KEY=$fk"; break }
            default { $_ }
        }
    }
    [System.IO.File]::WriteAllLines("$root\.env", $lines)
    Log ".env créé à partir de .env.example avec des secrets aléatoires (non affichés)."
} else {
    Log ".env existant conservé tel quel."
}
$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2; $envVars[$k.Trim()] = $v.Trim()
}
foreach ($k in $envVars.Keys) {
    if ($k -match 'PASSWORD|SECRET|SIGNING|ENCRYPTION|API_KEY|TOKEN' -or $k -in @('DATABASE_URL','REDIS_URL')) {
        if ($envVars[$k]) { $script:secrets += $envVars[$k] }
    }
}

$required = 'POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD','REDIS_PASSWORD','S3_ACCESS_KEY','S3_SECRET_KEY','DATABASE_URL','JWT_SIGNING_KEY'
$missing = $required | Where-Object { -not $envVars[$_] }
$weak = $required | Where-Object { $envVars[$_] -match 'change-me' }
Log ("Variables requises présentes : " + ($required.Count - $missing.Count) + "/" + $required.Count)
Result "env_variables" (($missing.Count -eq 0) -and ($weak.Count -eq 0)) ("manquantes: " + ($missing -join ',') + " ; valeurs par défaut: " + ($weak -join ','))
$paymentsOff = ($envVars['PAYMENTS_ENABLED'] -eq 'false') -and (-not $envVars['STRIPE_SECRET_KEY'])
Result "paiements_desactives" $paymentsOff
$llmOff = ($envVars['LLM_PROVIDER'] -eq 'disabled') -and (-not $envVars['LLM_API_KEY'])
Result "aucune_cle_ia_payante" $llmOff

$compose = @("compose", "-f", "infra/docker-compose.yml", "--env-file", ".env")
$PGU = $envVars['POSTGRES_USER']; $PGD = $envVars['POSTGRES_DB']
if ($envVars['POSTGRES_PORT'] -eq '5432') {
    Log "Attention : POSTGRES_PORT=5432 dans .env. Ce port est souvent occupé ou réservé sous Windows ; 55432 est recommandé."
}

# -----------------------------------------------------------------------------
# 3. Démarrage et santé des services
# -----------------------------------------------------------------------------
$r = Run "docker compose config" { docker @compose config -q }
Result "compose_config" ($r.Code -eq 0)
$r = Run "docker compose up -d" { docker @compose up -d }
Result "compose_up" ($r.Code -eq 0)
if ($r.Code -ne 0) { Log "Échec du démarrage : voir les erreurs ci-dessus (téléchargement d'image, port occupé...)." }
if ($r.Out -match "ports are not available") {
    Log "Port hôte refusé par Windows. Dans .env, utilisez par exemple POSTGRES_PORT=55432 et le même port dans DATABASE_URL,"
    Log "puis relancez le script. Plages réservées par Windows : netsh interface ipv4 show excludedportrange protocol=tcp"
}

Log "Attente des health checks (max 120 s)..."
$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 3
    $pgH = docker inspect -f "{{.State.Health.Status}}" (docker @compose ps -q postgres) 2>$null
    $rdH = docker inspect -f "{{.State.Health.Status}}" (docker @compose ps -q redis) 2>$null
    $s3H = docker inspect -f "{{.State.Health.Status}}" (docker @compose ps -q s3) 2>$null
} while ((($pgH -ne "healthy") -or ($rdH -ne "healthy") -or ($s3H -ne "healthy")) -and ((Get-Date) -lt $deadline))
Run "docker compose ps" { docker @compose ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}\t{{.Image}}" } | Out-Null
Result "postgres_healthy" ($pgH -eq "healthy") $pgH
Result "redis_healthy" ($rdH -eq "healthy") $rdH
Result "s3_healthy" ($s3H -eq "healthy") $s3H
foreach ($svc in 's3','mailpit') {
    $st = docker inspect -f "{{.State.Status}}" (docker @compose ps -q $svc) 2>$null
    Result "${svc}_running" ($st -eq "running") $st
}
try { $m = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 http://localhost:9000/health; Result "s3_health_http" ($m.StatusCode -eq 200) } catch { Result "s3_health_http" $false $_.Exception.Message }
try { $m = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 http://localhost:8025/livez; Result "mailpit_http" ($m.StatusCode -eq 200) } catch { Result "mailpit_http" $false $_.Exception.Message }
$r = Run "Redis : PING authentifié" { docker @compose exec -T -e REDISCLI_AUTH=$($envVars['REDIS_PASSWORD']) redis redis-cli ping }
Result "redis_ping" ($r.Out -match "PONG")

# -----------------------------------------------------------------------------
# 4. PostgreSQL : connexion, pgvector, persistance
# -----------------------------------------------------------------------------
function Psql([string]$db, [string]$sql) { docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PGU -d $db -Atc $sql }
$r = Run "PostgreSQL : connexion et version" { Psql $PGD "SELECT version()" }
Result "postgres_connexion" ($r.Code -eq 0)
$r = Run "pgvector disponible" { Psql $PGD "SELECT default_version FROM pg_available_extensions WHERE name='vector'" }
Result "pgvector_disponible" ($r.Code -eq 0 -and $r.Out.Trim() -ne "") $r.Out

$stamp = [guid]::NewGuid().ToString()
Run "Persistance : base jetable + écriture" {
    Psql "postgres" "DROP DATABASE IF EXISTS nl_validation_persist"
    Psql "postgres" "CREATE DATABASE nl_validation_persist"
    Psql "nl_validation_persist" "CREATE TABLE t(v text); INSERT INTO t VALUES ('$stamp')"
} | Out-Null
Run "Persistance : redémarrage du conteneur postgres" { docker @compose restart postgres } | Out-Null
$deadline = (Get-Date).AddSeconds(60)
do { Start-Sleep -Seconds 2; $pgH = docker inspect -f "{{.State.Health.Status}}" (docker @compose ps -q postgres) 2>$null } while ($pgH -ne "healthy" -and (Get-Date) -lt $deadline)
$r = Run "Persistance : relecture après redémarrage" { Psql "nl_validation_persist" "SELECT v FROM t" }
Result "volume_persistance" ($r.Out -match $stamp)
Psql "postgres" "DROP DATABASE IF EXISTS nl_validation_persist" | Out-Null
$r = Run "Volumes nommés" { docker volume ls --filter "name=nurselearn_" --format "{{.Name}}" }
Result "volumes_nommes" (($r.Out -match "pgdata") -and ($r.Out -match "redisdata") -and ($r.Out -match "s3data"))

# -----------------------------------------------------------------------------
# 5. Alembic RÉEL + pgvector sur une base jetable
# -----------------------------------------------------------------------------
Psql "postgres" "DROP DATABASE IF EXISTS nl_validation_alembic" | Out-Null
Psql "postgres" "CREATE DATABASE nl_validation_alembic" | Out-Null
$net = "nurselearn_default"
$dbUrl = "postgresql+psycopg://${PGU}:$($envVars['POSTGRES_PASSWORD'])@postgres:5432/nl_validation_alembic"
$py = "python:3.12-slim"
# Guillemets simples uniquement : PowerShell 5.1 transmet mal les guillemets doubles imbriqués.
$pipAlembic = "pip install -q --disable-pip-version-check --root-user-action=ignore 'alembic>=1.13,<2' 'sqlalchemy>=2.0,<3' 'psycopg[binary]>=3.2,<4'"
function Alembic([string]$args_) {
    docker run --rm --network $net -v "${root}:/repo" -w /repo/services/api -e DATABASE_URL=$dbUrl -e PYTHONDONTWRITEBYTECODE=1 $py sh -c "$pipAlembic && alembic $args_"
}
function Verify([string]$file) { docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $PGU -d nl_validation_alembic -f "/database/checks/$file" }

$r = Run "Alembic : upgrade head" { Alembic "upgrade head && alembic current" }
Result "alembic_upgrade_head" ($r.Code -eq 0 -and $r.Out -match "0003_business_rules")
$r = Run "Vérification de la tête (60 tables, FK, pgvector, HNSW, 0003)" { Verify "verify_head.sql" }
Result "alembic_head_verifie" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "OK :") -join "")
$r = Run "Alembic : upgrade head (idempotence)" { Alembic "upgrade head && alembic current" }
Result "alembic_idempotent" ($r.Code -eq 0)
$r = Run "Données de référence appliquées deux fois" {
    docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U $PGU -d nl_validation_alembic -f /database/seeds/0001_reference_data.sql
    docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U $PGU -d nl_validation_alembic -f /database/seeds/0001_reference_data.sql
    Psql "nl_validation_alembic" "SELECT count(*) FROM professions"
}
Result "seeds_idempotents" ($r.Code -eq 0 -and $r.Out.Trim().EndsWith("12"))
$r = Run "Alembic : downgrade base" { Alembic "downgrade base" }
Result "alembic_downgrade_base" ($r.Code -eq 0)
$downOk = ($r.Code -eq 0)
$r = Run "Vérification base vide" { Verify "verify_base.sql" }
# Une base vide ne prouve rien si le retour arrière lui-même a échoué.
Result "alembic_base_vide" ($downOk -and $r.Code -eq 0)
$r = Run "Alembic : upgrade head (réapplication)" { Alembic "upgrade head" }
$r2 = Run "Vérification après réapplication" { Verify "verify_head.sql" }
Result "alembic_reapplication" ($r.Code -eq 0 -and $r2.Code -eq 0)
Psql "postgres" "DROP DATABASE IF EXISTS nl_validation_alembic WITH (FORCE)" | Out-Null

# -----------------------------------------------------------------------------
# 6. Suites de tests pytest contre le PostgreSQL + pgvector du compose
# -----------------------------------------------------------------------------
$r = Run "pytest : schéma, règles métier, migrations (pgvector inclus)" {
    docker run --rm --network $net -v "${root}:/repo" -w /repo `
        -e NL_TEST_PGHOST=postgres -e NL_TEST_PGPORT=5432 -e NL_TEST_PGUSER=$PGU -e PGPASSWORD=$($envVars['POSTGRES_PASSWORD']) `
        -e PYTHONDONTWRITEBYTECODE=1 $py sh -c "apt-get update -qq >/dev/null && apt-get install -y -qq postgresql-client >/dev/null && pip install -q --disable-pip-version-check --root-user-action=ignore 'pytest>=8,<10' && python -m pytest -q -rs -p no:cacheprovider database/tests services/api/tests"
}
Result "pytest_base_de_donnees" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "passed|failed|skipped") -join " ")
Result "pgvector_test_non_ignore" (($r.Code -eq 0) -and ($r.Out -match "passed") -and -not ($r.Out -match "skipped"))

# Design system (si Node.js est installé sur le poste)
if (Get-Command node -ErrorAction SilentlyContinue) {
    Push-Location "$root\packages\design-tokens"
    $r = Run "Design system : contrastes WCAG" { node src/build.mjs; node --test "test/*.test.mjs" }
    Pop-Location
    Result "design_tokens" ($r.Code -eq 0) (($r.Out -split "`n" | Select-String "^# (pass|fail)") -join " ")
} else { Log "Node.js absent : tests du design system non exécutés (couverts par la CI)." }

# -----------------------------------------------------------------------------
# 7. Absence de secrets dans les journaux des conteneurs
# -----------------------------------------------------------------------------
$logs = (docker @compose logs --no-color 2>&1 | Out-String)
$leaks = 0
foreach ($k in 'POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SIGNING_KEY') {
    $v = $envVars[$k]; if ($v -and $logs.Contains($v)) { $leaks++; Log "    secret $k trouvé dans les journaux" }
}
# Des journaux vides (services arrêtés) ne prouvent rien : le contrôle échoue alors.
Result "aucun_secret_dans_les_journaux" (($leaks -eq 0) -and ($logs.Length -gt 1024)) "($leaks fuite(s), $([int]($logs.Length/1024)) Ko analysés)"

# -----------------------------------------------------------------------------
# Synthèse
# -----------------------------------------------------------------------------
Log ""
Log "================ SYNTHÈSE ================"
$fail = 0
foreach ($k in $results.Keys) {
    $tag = if ($results[$k].Ok) { "PASS" } else { "FAIL"; $fail++ }
    Log ("{0,-34} {1}" -f $k, $tag)
}
Log ("Total : {0} contrôles, {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
Log "Les services restent démarrés. Pour les arrêter sans perdre de données : docker compose -f infra/docker-compose.yml --env-file .env stop"
if ($fail -gt 0) { exit 1 } else { exit 0 }

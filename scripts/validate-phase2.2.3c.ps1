# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.3-c (antivirus ClamAV et analyse asynchrone)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.3c-antivirus-worker) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3c.ps1
#
# 0. prépare le mot de passe de la connexion de service PostgreSQL (MEDIA_SCANNER_DB_PASSWORD) dans
#    .env s'il manque (valeur aléatoire, jamais affichée) ;
# 1. non-régression complète : scripts\validate-phase2.2.3b.ps1 (enchaîne 2.2.3-a, 2.2.2-c … 1.2 ;
#    la pile est reconstruite et l'API applique la migration 0008 au démarrage) ;
# 2. profil Docker « media » : freshclam, clamd, worker ; santé, signatures, réseaux, mémoire réelle ;
# 3. tests d'intégration et adversariaux sur PostgreSQL, Redis, RustFS et ClamAV RÉELS
#    (EICAR reconstitué à l'exécution, uniquement en quarantaine de test) ;
# 4. contrôles en LECTURE SEULE de la base de développement (états, permissions, identité de service).
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\phase2.2.3c-<date>.log
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
$log = Join-Path $logDir "phase2.2.3c-$stamp.log"
$results = [ordered]@{}
$secrets = @()

function Mask([string]$text) { foreach ($s in $script:secrets) { if ($s -and $s.Length -ge 6) { $text = $text.Replace($s, "***") } }; return $text }
function Log([string]$msg) { $line = Mask $msg; Write-Host $line; Add-Content -Path $script:log -Value $line -Encoding UTF8 }
function Result([string]$name, [bool]$ok, [string]$detail = "") {
    $script:results[$name] = $ok
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    Log "    => [$tag] $name $detail"
}
function ReadEnv {
    $vars = @{}
    Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $vars[$k.Trim()] = $v.Trim() }
    return $vars
}

Log "NurseLearn AI — validation étape 2.2.3-c — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_2_3c" ($branch -like "phase-2.2.3c*" -or $branch -like "security-*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
$hist = (git diff --diff-filter=MD --name-only origin/main...HEAD -- database/schema 2>$null) -join " "
Result "migrations_historiques_inchangees" ([string]::IsNullOrWhiteSpace($hist)) $hist
Result "migration_0008_presente" ((Test-Path "$root\database\schema\0008_media_scanner_role.sql") -and (Test-Path "$root\services\api\alembic\versions\0008_media_scanner_role.py")) ""

# -----------------------------------------------------------------------------
# 0. Mot de passe de la connexion de service (fichier .env local, jamais versionné)
# -----------------------------------------------------------------------------
$envVars = ReadEnv
if (-not $envVars['MEDIA_SCANNER_DB_PASSWORD']) {
    $bytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $pw = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    Add-Content -Path "$root\.env" -Value "`r`nMEDIA_SCANNER_DB_PASSWORD=$pw" -Encoding ASCII
    Log "    MEDIA_SCANNER_DB_PASSWORD absent de .env : valeur aléatoire ajoutée (non affichée)."
    $envVars = ReadEnv
}
foreach ($k in $envVars.Keys) { if ($k -match 'PASSWORD|SECRET|SIGNING|TOKEN|API_KEY' -and $envVars[$k]) { $script:secrets += $envVars[$k] } }
Result "mot_de_passe_service_present" ($envVars['MEDIA_SCANNER_DB_PASSWORD'].Length -ge 32) "longueur=$($envVars['MEDIA_SCANNER_DB_PASSWORD'].Length)"
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
$PGPW = $envVars['POSTGRES_PASSWORD']
$S3AK = if ($envVars['S3_ACCESS_KEY']) { $envVars['S3_ACCESS_KEY'] } else { "nurselearn" }
function Psql([string]$sql, [string]$db = $PGDB) { (docker compose exec -T postgres psql -U $PGU -d $db -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.2.3-b → … → 1.2) — applique 0008 (démarrage de l'API)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.3b.ps1 (enchaîne 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0 et 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.3b.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.3b-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_3b_et_anterieures" $false "aucun nouveau journal : validate-phase2.2.3b.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.3-b.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_3b_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
}
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0008_appliquee" ($rev -match '^000[89]_') $rev
$verify = (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -v ON_ERROR_STOP=1 -f /database/checks/verify_head.sql 2>&1 | Out-String)
Result "schema_tete_verifie" ($verify -match "OK : 75 tables") (($verify -split "`n" | Select-String "OK|ERR") -join " ")

# -----------------------------------------------------------------------------
# 2. Profil « media » : freshclam, clamd, worker
# -----------------------------------------------------------------------------
Log ""; Log ">>> docker compose --profile media up -d --build (premier démarrage : chargement des signatures)"
$out = (docker compose --profile media up -d --build 2>&1 | Out-String)
# Attente explicite (25 min au plus) : `up --wait` échoue quand le conteneur ponctuel `migrate` (D14) se termine.
$deadline = (Get-Date).AddMinutes(25)
do {
    Start-Sleep -Seconds 10
    $pending = @(foreach ($s in 'postgres', 'redis', 's3', 'api', 'web', 'freshclam', 'clamd', 'media-worker') { $h = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q $s) 2>$null; if ($h -ne "healthy") { $s } })
} while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline)
Log (($out -split "`n" | Select-Object -Last 8) -join "`n")
$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web', 'freshclam', 'clamd', 'media-worker') {
    $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q $s) 2>$null
}
Result "services_media_operationnels" (($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0) (($states.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

# Diagnostic du worker (lecture seule) : identité de service et signatures chargées par clamd.
function SelfCheck {
    $raw = (docker compose --profile media exec -T media-worker python -m app.workers.media_scan --self-check 2>&1 | ForEach-Object { "$_" })
    $line = $raw | Where-Object { $_ -like '{"identity"*' } | Select-Object -Last 1
    if ($line) { return ($line | ConvertFrom-Json) }
    Log "    self-check illisible : $(($raw | Select-Object -Last 3) -join ' ')"
    return $null
}
# Signatures : chargées et de moins de 48 h. Si elles sont périmées : journal de freshclam, mise à
# jour explicite (freshclam), rechargement de clamd, puis nouvelle attente (15 min au plus).
$sc = SelfCheck
if ($sc -and -not $sc.signatures_fresh) {
    Log "    Signatures chargées périmées ($($sc.signature_age_hours) h) : diagnostic de freshclam"
    foreach ($l in (docker compose --profile media logs --no-color --tail 40 freshclam 2>&1 | ForEach-Object { "$_" })) { Log "      $l" }
    Log "    >>> Mise à jour explicite des signatures (freshclam)"
    foreach ($l in (docker compose --profile media exec -T freshclam freshclam --stdout 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 15)) { Log "      $l" }
    Log "    >>> Rechargement de clamd"
    foreach ($l in (docker compose --profile media exec -T clamd clamdscan --reload 2>&1 | ForEach-Object { "$_" })) { Log "      $l" }
    $deadline = (Get-Date).AddMinutes(15)
    do { Start-Sleep -Seconds 20; $sc = SelfCheck } while ((-not $sc -or -not $sc.signatures_fresh) -and (Get-Date) -lt $deadline)
}
if ($sc) { Log "    ClamAV : moteur $($sc.engine_version), signatures $($sc.signature_version) publiées le $($sc.signatures_published_at) (âge $($sc.signature_age_hours) h)" }
Result "clamav_signatures_fraiches" ($sc -and $sc.signatures_fresh) "âge=$(if ($sc) { $sc.signature_age_hours }) h"

# Réseaux : clamd uniquement sur le réseau interne, aucun port publié, injoignable depuis l'API.
$clamdId = docker compose --profile media ps -q clamd
$nets = (docker inspect -f "{{range `$k, `$v := .NetworkSettings.Networks}}{{`$k}} {{end}}" $clamdId).Trim()
$internal = docker network inspect -f "{{.Internal}}" "nurselearn_media-scan" 2>$null
$ports = (docker inspect -f "{{json .NetworkSettings.Ports}}" $clamdId)
Result "clamd_reseau_interne_sans_port_publie" ($nets -eq "nurselearn_media-scan" -and $internal -eq "true" -and $ports -notmatch "HostPort") "réseaux=$nets interne=$internal"
$probe = (docker compose exec -T api python -c "import socket; socket.create_connection(('clamd', 3310), timeout=3); print('JOIGNABLE')" 2>&1 | Out-String)
Result "clamd_injoignable_depuis_l_api" ($probe -notmatch "JOIGNABLE") ""
$fcNets = (docker inspect -f "{{range `$k, `$v := .NetworkSettings.Networks}}{{`$k}} {{end}}" (docker compose --profile media ps -q freshclam)).Trim()
Result "freshclam_isole_des_services" ($fcNets -eq "nurselearn_clamav-updates") "réseaux=$fcNets"

# Identité du worker : connexion de service, jamais superutilisateur, sans accès à l'audit.
$id = if ($sc) { ($sc.identity | ForEach-Object { "$_" }) -join " " } else { "" }
Result "worker_identite_de_service_minimale" ($id -eq "nl_media_scanner False False False") "session, superutilisateur, lecture audit, lecture e-mails = $id"

# Mémoire réelle (après chargement des signatures).
$stats = (docker stats --no-stream --format "{{.Name}}|{{.MemUsage}}" 2>$null | Where-Object { $_ -match "clamd|freshclam|media-worker" })
foreach ($l in $stats) { Log "    mémoire : $l" }
$clamdMem = ($stats | Where-Object { $_ -match "clamd" } | Select-Object -First 1)
$mib = 0.0
if ($clamdMem -match '\|([0-9.]+)(GiB|MiB)') { $mib = [double]$Matches[1]; if ($Matches[2] -eq "GiB") { $mib *= 1024 } }
Result "memoire_clamd_mesuree_sous_limite" ($mib -gt 100 -and $mib -lt 3072) ("clamd={0:N0} Mio (limite 3 072)" -f $mib)

# -----------------------------------------------------------------------------
# 3. Tests d'intégration et adversariaux (PostgreSQL, Redis, RustFS, ClamAV réels)
# -----------------------------------------------------------------------------
Psql "DROP DATABASE IF EXISTS nl_validation_scan WITH (FORCE)" "postgres" | Out-Null
Psql "CREATE DATABASE nl_validation_scan" "postgres" | Out-Null
Log ""; Log ">>> pytest : worker d'analyse (EICAR, pannes, reprise, concurrence), rôle de service, téléversement"
$cid = (docker create -v "${root}:/repo" -w /repo --network nurselearn_default `
    -e "DATABASE_URL=postgresql+asyncpg://${PGU}:${PGPW}@postgres:5432/nl_validation_scan" `
    -e "REDIS_URL=redis://:$($envVars['REDIS_PASSWORD'])@redis:6379/0" `
    -e NL_TEST_S3_ENDPOINT=http://s3:9000 -e NL_TEST_S3_KIND=rustfs -e "S3_ACCESS_KEY=$S3AK" -e "S3_SECRET_KEY=$($envVars['S3_SECRET_KEY'])" `
    -e NL_TEST_CLAMD_HOST=clamd -e NL_TEST_CLAMD_PORT=3310 -e "NL_TEST_SCANNER_PASSWORD=$($envVars['MEDIA_SCANNER_DB_PASSWORD'])" `
    -e "API_DB_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_API_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_ADMIN_DATABASE_URL=postgresql+psycopg://${PGU}:${PGPW}@postgres:5432/nl_validation_scan" `
    -e NL_TEST_PGHOST=postgres -e NL_TEST_PGPORT=5432 -e NL_TEST_PGUSER=$PGU -e PGPASSWORD=$PGPW -e DEBIAN_FRONTEND=noninteractive -e PYTHONDONTWRITEBYTECODE=1 `
    python:3.12-slim sh -c "apt-get update -qq >/dev/null && apt-get install -y -qq postgresql-client >/dev/null && cd services/api && pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]' && alembic upgrade head && python -m app.seed && cd /repo && python -m pytest -q -rs -p no:cacheprovider database/tests/test_media_scanner_role.py && DATABASE_URL=postgresql+asyncpg://nl_api:$($envVars['API_DB_PASSWORD'])@postgres:5432/nl_validation_scan python -m pytest -q -rs -p no:cacheprovider services/api/tests/api/test_media_scan.py services/api/tests/api/test_media_upload.py" 2>&1).Trim()
docker network connect nurselearn_media-scan $cid 2>&1 | Out-Null
$pyOut = (docker start -a $cid 2>&1 | Out-String)
$pyCode = [int](docker inspect -f "{{.State.ExitCode}}" $cid)
docker rm $cid 2>&1 | Out-Null
Psql "DROP DATABASE IF EXISTS nl_validation_scan WITH (FORCE)" "postgres" | Out-Null
foreach ($l in ($pyOut -split "`n" | Where-Object { $_ -notmatch '^\{"ts"' } | Select-Object -Last 25)) { Log "    $l" }
# Deux exécutions (base, puis API : leurs conftest sont distincts) ; chacune sans échec ni test ignoré.
$lines = @($pyOut -split "`n" | Where-Object { $_ -match '^\s*=*\s*\d+ (passed|failed|errors?)\b' })
$okRuns = @($lines | Where-Object { "$_" -match "passed" -and "$_" -notmatch "skipped|failed|error" }).Count
Result "tests_worker_clamav_reel" ($pyCode -eq 0 -and $okRuns -eq 2) (($lines | ForEach-Object { "$_".Trim() }) -join " | ")
Result "eicar_detecte_par_clamav_reel" ($pyCode -eq 0 -and $pyOut -notmatch "test_eicar\w* (FAILED|SKIPPED)") ""

# -----------------------------------------------------------------------------
# 4. Contrôles en lecture seule de la base de développement
# -----------------------------------------------------------------------------
$roles = Psql "SELECT string_agg(r.code, ',') FROM role_permissions rp JOIN roles r ON r.id = rp.role_id WHERE rp.permission_code = 'media:scan'"
Result "media_scan_reservee_a_media_scanner" ($roles -eq "media_scanner") $roles
$perms = Psql "SELECT string_agg(rp.permission_code, ',') FROM role_permissions rp JOIN roles r ON r.id = rp.role_id WHERE r.code = 'media_scanner'"
Result "media_scanner_sans_autre_permission" ($perms -eq "media:scan") $perms
$acct = Psql "SELECT (password_hash IS NULL) || ' ' || (SELECT string_agg(r.code, ',') FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = u.id) || ' ' || (SELECT count(*) FROM auth_sessions s WHERE s.user_id = u.id) FROM users u WHERE id = '00000000-0000-4000-a000-00000000c1a5'"
Result "compte_de_service_sans_mot_de_passe_ni_session" ($acct -eq "true media_scanner 0") $acct
$login = Psql "SELECT rolsuper || ' ' || rolcreaterole || ' ' || rolcreatedb || ' ' || rolbypassrls || ' ' || rolcanlogin FROM pg_roles WHERE rolname = 'nl_media_scanner'"
Result "connexion_service_non_privilegiee" ($login -eq "false false false false true") $login
$untraceable = Psql "SELECT count(*) FROM media_assets m WHERE m.state = 'clean' AND NOT EXISTS (SELECT 1 FROM media_scan_events e WHERE e.media_id = m.id AND e.attempt = m.scan_attempt AND e.result = 'clean' AND e.sha256 = m.sha256 AND e.signatures_published_at >= e.scanned_at - interval '48 hours')"
Result "aucun_media_sain_sans_verdict_valide" ($untraceable -eq "0") "médias clean sans verdict valide=$untraceable"
# Session de l'API (superutilisateur de développement) déclarant l'acteur de service : verdict refusé.
$probe = Psql "BEGIN; SELECT set_config('nl.actor_id', '00000000-0000-4000-a000-00000000c1a5', true); INSERT INTO media_scan_events (media_id, attempt, result, engine, sha256, scanned_by) VALUES (gen_random_uuid(), 1, 'clean', 'clamav', repeat('a', 64), '00000000-0000-4000-a000-00000000c1a5'); ROLLBACK;"
Result "verdict_force_refuse_hors_connexion_de_service" ($probe -match "nl_media_scanner") "sonde annulée"
$probe = Psql "BEGIN; TRUNCATE media_scan_events; ROLLBACK;"
$guards = Psql "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_media_scan_events_guard', 'trg_media_scan_events_no_truncate', 'trg_media_scan_events_05_scanner')"
Result "journal_analyses_ajout_seul" ($probe -match "ERROR|ERREUR" -and $guards -eq "3") "gardes=$guards, sonde annulée"
$probe = Psql "BEGIN; SELECT set_config('nl.role_grant_bypass', 'on', true); INSERT INTO user_roles (user_id, role_id) SELECT id, (SELECT id FROM roles WHERE code = 'media_scanner') FROM users WHERE id <> '00000000-0000-4000-a000-00000000c1a5' LIMIT 1; ROLLBACK;"
Result "role_media_scanner_non_attribuable" ($probe -match "compte de service") "sonde annulée"
$routes = (docker compose exec -T api python -c "from app.main import create_app; p = create_app().openapi()['paths']; print(sorted(f'{m.upper()} {k}' for k, v in p.items() if k.startswith('/api/v1/editorial/media') for m in v))" 2>&1 | Out-String).Trim()
Result "aucune_route_de_diffusion" ($routes -eq "['DELETE /api/v1/editorial/media/{media_id}', 'GET /api/v1/editorial/media', 'GET /api/v1/editorial/media/{media_id}', 'POST /api/v1/editorial/media', 'PUT /api/v1/editorial/media/{media_id}/content']") $routes
try { $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9000/nl-media-clean/" -ErrorAction Stop; $code = [int]$r.StatusCode } catch { $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { -1 } }
Result "stockage_sain_illisible_anonymement" ($code -eq 403 -or $code -eq 404) "statut $code"
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"
$pilot = Psql "SELECT status FROM programs WHERE code = 'pab-qc-socle'"
Result "pilote_pab_en_brouillon" ($pilot -eq "draft") $pilot
$wlogs = (docker compose --profile media logs --no-color media-worker 2>&1 | Out-String)
$leak = $false; foreach ($v in $script:secrets) { if ($v.Length -ge 6 -and $wlogs.Contains($v)) { $leak = $true } }
Result "journaux_worker_sans_secret_ni_erreur_de_configuration" ((-not $leak) -and ($wlogs -notmatch "configuration_invalide")) ""

Log ""; Log "================ SYNTHÈSE ÉTAPE 2.2.3-c ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.3-c (+ non-régression 2.2.3-b, 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

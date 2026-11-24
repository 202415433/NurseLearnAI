# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.3-e (médias protégés : rattachement, conservation légale,
# FreshClam, interfaces, validation de bout en bout)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.3e-media-management-ui) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3e.ps1 -Stage e1
#
# -Stage e1 (point de contrôle E1) :
# 0. état Git (migrations historiques inchangées, seule 0010 ajoutée) ; secrets présents dans .env ;
# 1. INVENTAIRE avant toute modification : volumes, compteurs PostgreSQL, objets S3 ;
# 2. non-régression complète : scripts\validate-phase2.2.3d.ps1 (2.2.3-d → R3 → D14 → 2.2.3-c … 1.2) ;
#    la pile est reconstruite et le conteneur `migrate` applique la migration 0010 ;
# 3. migration 0010 : en-tête, verify_head (76 tables), privilèges (aucune écriture directe de l'API) ;
# 4. FreshClam → clamd : socket dédié, notification depuis freshclam (sans TCP), rechargement effectif
#    (version chargée = version sur disque), redémarrages, FreshClam indisponible (fraîcheur exigée) ;
# 5. pytest sur la pile réelle : SQL adversariaux 0010, D14, rattachement, conservation légale,
#    éditions figées, ClamAV réel et EICAR, signatures absentes/périmées, diffusion, édition ;
# 6. intégrité : aucun volume, aucune donnée, aucun objet perdu ; aucune publication médicale.
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\phase2.2.3e-<date>.log
# =============================================================================
param([ValidateSet('e1')][string]$Stage = 'e1')
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# Sans BOM : la sonde S3 est transmise à Python par l'entrée standard des conteneurs.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$root = (Get-Location).Path
if (-not (Test-Path "$root\compose.yaml")) { Write-Error "Lancer depuis la racine du dépôt (C:\NurseLearnAI-git)."; exit 2 }
if (-not (Test-Path "$root\.env")) { Write-Error ".env absent."; exit 2 }
$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "phase2.2.3e-$stamp.log"
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
function NewSecret([int]$bytes = 24) { $b = New-Object byte[] $bytes; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); return (-join ($b | ForEach-Object { $_.ToString("x2") })) }
function Json([string]$raw) { try { return (($raw -split "`n" | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json) } catch { return $null } }
function HttpCode([string]$uri, [hashtable]$headers = @{}) {
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri $uri -Headers $headers -ErrorAction Stop; return [int]$r.StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return -1 } }
}

Log "NurseLearn AI — validation 2.2.3-e, étape $Stage (médias : rattachement, conservation, FreshClam, interfaces) — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_2_2_3d" ($branch -like "phase-2.2.3[d-z]*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
$hist = (git diff --diff-filter=MD --name-only origin/main...HEAD -- database/schema services/api/alembic/versions 2>$null) -join " "
Result "migrations_historiques_inchangees" ([string]::IsNullOrWhiteSpace($hist)) $hist
$added = (git diff --diff-filter=A --name-only origin/main...HEAD -- database/schema services/api/alembic/versions 2>$null) -join " "
Result "seule_migration_ajoutee_0010" ($added -eq "database/schema/0010_media_editorial_permissions.sql database/schema/0010_media_editorial_permissions_down.sql services/api/alembic/versions/0010_media_editorial_permissions.py") $added
Result "fichiers_e1_presents" ((Test-Path "$root\services\api\app\domains\media\attachments.py") -and (Test-Path "$root\infra\clamav\clamd.conf") -and (Test-Path "$root\database\tests\test_media_editorial_permissions.py") -and (Test-Path "$root\services\api\tests\api\test_media_attachments.py")) ""

# -----------------------------------------------------------------------------
# 0. Secrets (fichier .env local, jamais versionné)
# -----------------------------------------------------------------------------
$envVars = ReadEnv
foreach ($k in 'S3_API_SECRET_KEY', 'S3_SCANNER_SECRET_KEY', 'S3_READER_SECRET_KEY', 'API_DB_PASSWORD', 'MEDIA_SCANNER_DB_PASSWORD') {
    if (-not $envVars[$k]) {
        $v = if ($k -like 'S3_*') { NewSecret 16 } else { NewSecret 24 }
        if ((Get-Content "$root\.env") -match "^\s*$k=") {
            (Get-Content "$root\.env") -replace "^\s*$k=.*", "$k=$v" | Set-Content "$root\.env" -Encoding ASCII
        } else {
            Add-Content -Path "$root\.env" -Value "`r`n$k=$v" -Encoding ASCII
        }
        Log "    $k absent de .env : valeur aléatoire ajoutée (non affichée)."
        $envVars = ReadEnv
    }
}
foreach ($k in $envVars.Keys) { if ($k -match 'PASSWORD|SECRET|SIGNING|TOKEN|API_KEY' -and $envVars[$k]) { $script:secrets += $envVars[$k] } }
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
$PGPW = $envVars['POSTGRES_PASSWORD']
$S3AK = if ($envVars['S3_ACCESS_KEY']) { $envVars['S3_ACCESS_KEY'] } else { "nurselearn" }
$rootSk = $envVars['S3_SECRET_KEY']
$dPort = if ($envVars['MEDIA_DELIVERY_PORT']) { $envVars['MEDIA_DELIVERY_PORT'] } else { "28001" }
$aPort = if ($envVars['API_PORT']) { $envVars['API_PORT'] } else { "28000" }
function Psql([string]$sql, [string]$db = $PGDB) { (docker compose exec -T postgres psql -U $PGU -d $db -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }
function Ops([string]$opt) { (docker compose run --rm --no-deps -T s3-init python -m app.ops.s3_provision $opt 2>&1 | ForEach-Object { "$_" }) -join "`n" }
$COUNTS = "SELECT (SELECT count(*) FROM users) || ' ' || (SELECT count(*) FROM user_roles) || ' ' || (SELECT count(*) FROM organizations) || ' ' || (SELECT count(*) FROM programs) || ' ' || (SELECT count(*) FROM lesson_versions) || ' ' || (SELECT count(*) FROM program_releases) || ' ' || (SELECT count(*) FROM media_assets) || ' ' || (SELECT count(*) FROM media_scan_events) || ' ' || (SELECT count(*) FROM editorial_decisions) || ' ' || (SELECT count(*) FROM audit_logs)"

# -----------------------------------------------------------------------------
# 1. Inventaire AVANT modification
# -----------------------------------------------------------------------------
Log ""; Log ">>> Inventaire avant validation"
$volsBefore = @(docker volume ls --format "{{.Name}}" 2>$null | Where-Object { $_ -like "nurselearn*" } | Sort-Object)
Log "    volumes : $($volsBefore -join ', ')"
docker compose up -d --wait postgres s3 2>&1 | Out-Null
$countsBefore = Psql $COUNTS
Log "    base (users user_roles organizations programs lesson_versions program_releases media_assets media_scan_events editorial_decisions audit_logs) = $countsBefore"
docker compose build s3-init 2>&1 | Out-Null
$invBefore = Json (Ops "--inventory")
Log "    objets S3 : $(if ($invBefore) { $invBefore | ConvertTo-Json -Compress } else { 'illisible' })"

# -----------------------------------------------------------------------------
# 2. Non-régression complète : 2.2.3-d → R3 → D14 → 2.2.3-c → … → 1.2 (pile reconstruite : 0010 appliquée)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.3d.ps1 (2.2.3-d, R3, D14, 2.2.3-c … 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.3d.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.3d-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_3d_et_anterieures" $false "aucun nouveau journal"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.3-d.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_3d_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
    foreach ($l in (Select-String -Path $reg.FullName -Pattern "non_regression_r3|tests_diffusion|tests_http_range|tests_revocation|tests_adversariaux" | ForEach-Object { $_.Line.Trim() })) { Log "    $l" }
}

# -----------------------------------------------------------------------------
# 3. E1 — migration 0010 et privilèges (D3-3, conservation légale)
# -----------------------------------------------------------------------------
Log ""; Log ">>> E1 : migration 0010 et privilèges PostgreSQL"
docker compose --profile media up -d 2>&1 | Out-Null
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0010_appliquee" ($rev -eq "0010_media_editorial_permissions") $rev
$verify = (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -v ON_ERROR_STOP=1 -f /database/checks/verify_head.sql 2>&1 | ForEach-Object { "$_" }) -join " "
Result "verify_head_76_tables_0010" ($verify -match "OK : 76 tables") (($verify -replace '\s+', ' ').Trim())
$priv = Psql "SELECT has_table_privilege('nl_api','lesson_version_media','INSERT') || ',' || has_table_privilege('nl_api','lesson_version_media','DELETE') || ',' || has_column_privilege('nl_api','media_assets','legal_hold','UPDATE') || ',' || has_table_privilege('nl_api','media_legal_hold_events','INSERT') || ',' || has_function_privilege('nl_api','nl_media_attach(uuid,uuid)','EXECUTE') || ',' || has_function_privilege('nl_api','nl_media_detach(uuid,uuid)','EXECUTE') || ',' || has_function_privilege('nl_api','nl_media_set_legal_hold(uuid,boolean,text)','EXECUTE') || ',' || has_function_privilege('public','nl_media_attach(uuid,uuid)','EXECUTE')"
Result "api_sans_ecriture_directe_fonctions_controlees" ($priv -eq "false,false,false,false,true,true,true,false") "INSERT/DELETE rattachements, UPDATE legal_hold, INSERT journal, EXECUTE x3, PUBLIC = $priv"
$holdRoles = Psql "SELECT string_agg(r.code, ',') FROM role_permissions rp JOIN roles r ON r.id = rp.role_id WHERE rp.permission_code = 'media:legal_hold'"
Result "permission_media_legal_hold_admin_seulement" ($holdRoles -eq "admin") $holdRoles

# -----------------------------------------------------------------------------
# 4. E1 — FreshClam → clamd : socket dédié, notification, rechargement effectif, redémarrages
# -----------------------------------------------------------------------------
Log ""; Log ">>> E1 : notification FreshClam → clamd"
for ($i = 0; $i -lt 60; $i++) { $h = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q clamd) 2>$null; if ($h -eq "healthy") { break }; Start-Sleep -Seconds 5 }
function Dc([string]$svc, [string[]]$cmd, [string]$user = "") {
    $args2 = @('compose', '--profile', 'media', 'exec', '-T') + $(if ($user) { @('-u', $user) } else { @() }) + @($svc) + $cmd
    $out = (& docker @args2 2>&1 | Out-String).Trim()
    return @{ Code = $LASTEXITCODE; Out = $out }
}
$sock = Dc clamd @('stat', '-c', '%U:%G %a %F', '/run/clamav/clamd.sock')
$dir = Dc clamd @('stat', '-c', '%U %a', '/run/clamav')
$tmp = Dc clamd @('test', '-S', '/tmp/clamd.sock')
Result "socket_dedie_permissions_minimales" ($sock.Out -eq "clamav:clamav 660 socket" -and $dir.Out -eq "clamav 750" -and $tmp.Code -ne 0) "socket=$($sock.Out) ; répertoire=$($dir.Out) ; /tmp/clamd.sock absent=$($tmp.Code -ne 0)"
$fcNet = (docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' (docker compose --profile media ps -q freshclam) 2>$null | Out-String).Trim()
$ping = Dc freshclam @('clamdscan', '--config-file=/etc/clamav/clamd.conf', '--ping', '5') 'clamav'
Result "notification_par_le_socket_depuis_freshclam" ($ping.Code -eq 0 -and $fcNet -notmatch 'media-scan') "ping=$($ping.Code) ; réseaux freshclam=$fcNet (aucun accès TCP à clamd)"
function Loaded { (Dc media-worker @('python', '-c', 'from app.core.config import get_settings as g; from app.workers.clamd import ClamdClient as C; s = g(); print(C(s.clamd_host, s.clamd_port, 10).version().signature_version)')).Out }
function OnDisk { ((Dc freshclam @('sh', '-c', 'for f in daily.cld daily.cvd; do [ -s /var/lib/clamav/$f ] && sigtool --info /var/lib/clamav/$f && break; done')).Out -split "`n" | Where-Object { $_ -match '^Version:' } | ForEach-Object { ($_ -split '\s+')[1] }) -join "" }
$before = @((docker compose --profile media logs clamd 2>&1 | Out-String) -split "`n" | Where-Object { $_ -match 'Database correctly reloaded' }).Count
$reload = Dc freshclam @('clamdscan', '--config-file=/etc/clamav/clamd.conf', '--reload') 'clamav'
$after = $before
for ($i = 0; $i -lt 60; $i++) {
    $after = @((docker compose --profile media logs clamd 2>&1 | Out-String) -split "`n" | Where-Object { $_ -match 'Database correctly reloaded' }).Count
    if ($after -gt $before -and (Loaded) -eq (OnDisk)) { break }
    Start-Sleep -Seconds 5
}
$ld = Loaded; $od = OnDisk
Result "rechargement_effectif_version_chargee_egale_disque" ($reload.Code -eq 0 -and $after -gt $before -and $ld -eq $od -and $ld) "RELOAD=$($reload.Out) ; rechargements $before -> $after ; chargée=$ld ; disque=$od"
$notified = ((docker compose --profile media logs freshclam 2>&1 | Out-String) -match 'Clamd successfully notified')
$tmpErr = ((docker compose --profile media logs --since 30m freshclam 2>&1 | Out-String) -match "Can't connect to clamd through /tmp/clamd.sock")
Result "freshclam_plus_aucun_echec_de_notification" (-not $tmpErr) "notification réussie observée=$notified (seulement si une mise à jour a eu lieu)"
docker compose --profile media restart clamd 2>&1 | Out-Null
for ($i = 0; $i -lt 120; $i++) { $h = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q clamd) 2>$null; if ($h -eq "healthy") { break }; Start-Sleep -Seconds 5 }
$p1 = Dc freshclam @('clamdscan', '--config-file=/etc/clamav/clamd.conf', '--ping', '5') 'clamav'
docker compose --profile media restart freshclam 2>&1 | Out-Null
for ($i = 0; $i -lt 60; $i++) { $h = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q freshclam) 2>$null; if ($h -eq "healthy") { break }; Start-Sleep -Seconds 5 }
$p2 = Dc freshclam @('clamdscan', '--config-file=/etc/clamav/clamd.conf', '--ping', '5') 'clamav'
Result "redemarrages_socket_recree" ($p1.Code -eq 0 -and $p2.Code -eq 0) "après redémarrage clamd=$($p1.Code) ; après redémarrage freshclam=$($p2.Code)"
docker compose --profile media stop freshclam 2>&1 | Out-Null
$sc = Dc media-worker @('python', '-m', 'app.workers.media_scan', '--self-check')
$scJson = Json $sc.Out
$hc = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q clamd) 2>$null
docker compose --profile media start freshclam 2>&1 | Out-Null
Result "freshclam_indisponible_clamd_continue_fraicheur_exigee" ($hc -eq "healthy" -and $scJson -and $scJson.signatures_fresh -eq $true -and $sc.Code -eq 0) "clamd=$hc ; signatures=$($scJson.signature_version) ($($scJson.signature_age_hours) h) ; fraîches=$($scJson.signatures_fresh)"

# -----------------------------------------------------------------------------
# 5. E1 — pytest sur la pile réelle (PostgreSQL, Redis, RustFS, ClamAV)
# -----------------------------------------------------------------------------
Log ""; Log ">>> E1 : pytest — SQL adversariaux 0010, D14, rattachement, conservation, ClamAV réel, diffusion"
Psql "DROP DATABASE IF EXISTS nl_validation_e1 WITH (FORCE)" "postgres" | Out-Null
Psql "CREATE DATABASE nl_validation_e1" "postgres" | Out-Null
$cid = (docker create -v "${root}:/repo" -w /repo --network nurselearn_default `
    -e "DATABASE_URL=postgresql+asyncpg://${PGU}:${PGPW}@postgres:5432/nl_validation_e1" `
    -e "REDIS_URL=redis://:$($envVars['REDIS_PASSWORD'])@redis:6379/0" `
    -e NL_TEST_S3_ENDPOINT=http://s3:9000 -e NL_TEST_S3_KIND=rustfs -e "S3_ACCESS_KEY=$S3AK" -e "S3_SECRET_KEY=$rootSk" `
    -e NL_TEST_CLAMD_HOST=clamd -e NL_TEST_CLAMD_PORT=3310 -e "NL_TEST_SCANNER_PASSWORD=$($envVars['MEDIA_SCANNER_DB_PASSWORD'])" `
    -e "API_DB_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_API_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_ADMIN_DATABASE_URL=postgresql+psycopg://${PGU}:${PGPW}@postgres:5432/nl_validation_e1" `
    -e NL_TEST_PGHOST=postgres -e NL_TEST_PGPORT=5432 -e NL_TEST_PGUSER=$PGU -e PGPASSWORD=$PGPW -e PYTHONDONTWRITEBYTECODE=1 -e DEBIAN_FRONTEND=noninteractive `
    python:3.12-slim sh -c "apt-get update -qq >/dev/null && apt-get install -y -qq postgresql-client >/dev/null && cd services/api && pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]' && alembic upgrade head && python -m app.seed && cd /repo && DATABASE_URL=postgresql+asyncpg://nl_api:$($envVars['API_DB_PASSWORD'])@postgres:5432/nl_validation_e1 python -m pytest -v -rs -p no:cacheprovider -p no:logging database/tests/test_media_editorial_permissions.py database/tests/test_postgres_roles.py database/tests/test_media_foundation.py database/tests/test_media_scanner_role.py services/api/tests/unit/test_clamav_freshness.py services/api/tests/api/test_media_attachments.py services/api/tests/api/test_media_delivery.py services/api/tests/api/test_media_scan.py services/api/tests/api/test_editorial.py" 2>&1).Trim()
docker network connect nurselearn_media-scan $cid 2>&1 | Out-Null
$pyOut = (docker start -a $cid 2>&1 | Out-String)
$pyCode = [int](docker inspect -f "{{.State.ExitCode}}" $cid)
docker rm $cid 2>&1 | Out-Null
Psql "DROP DATABASE IF EXISTS nl_validation_e1 WITH (FORCE)" "postgres" | Out-Null
$lines = @($pyOut -split "`n" | Where-Object { $_ -match '::test_\S+ (PASSED|FAILED|SKIPPED|ERROR)' } | ForEach-Object { $_.Trim() })
foreach ($l in ($pyOut -split "`n" | Where-Object { $_ -match '^=+ .*(passed|failed|error).* =+$' })) { Log "    $($l.Trim())" }
foreach ($l in ($lines | Where-Object { $_ -notmatch 'PASSED' })) { Log "    $l" }
function FileTests([string]$file) { @($lines | Where-Object { $_ -match [regex]::Escape($file) }) }
function AllPassed([object[]]$set, [int]$min) { $set.Count -ge $min -and @($set | Where-Object { $_ -notmatch 'PASSED' }).Count -eq 0 }
$sqlT = FileTests 'test_media_editorial_permissions.py'
Result "tests_sql_adversariaux_0010" (AllPassed $sqlT 11) "$($sqlT.Count) tests"
$d14T = FileTests 'test_postgres_roles.py'
Result "tests_d14_roles_postgresql" (AllPassed $d14T 10) "$($d14T.Count) tests"
$attT = FileTests 'test_media_attachments.py'
$attOnly = @($attT | Where-Object { $_ -notmatch 'legal_hold' })
$holdT = @($attT | Where-Object { $_ -match 'legal_hold' })
Result "tests_rattachement_api" (AllPassed $attOnly 5) "$($attOnly.Count) tests (dont éditions figées)"
Result "tests_conservation_legale_api" (AllPassed $holdT 2) "$($holdT.Count) tests"
$frozen = @($lines | Where-Object { $_ -match 'frozen' })
Result "tests_editions_figees" (AllPassed $frozen 3) "$($frozen.Count) tests (SQL, API, diffusion)"
$scanT = FileTests 'test_media_scan.py'
$eicar = @($scanT | Where-Object { $_ -match 'eicar' })
Result "tests_clamav_reels_et_eicar" ((AllPassed $scanT 10) -and (AllPassed $eicar 1)) "$($scanT.Count) tests (EICAR : $($eicar.Count))"
$freshT = FileTests 'test_clamav_freshness.py'
Result "tests_signatures_absentes_perimees" (AllPassed $freshT 9) "$($freshT.Count) tests"
$dlT = FileTests 'test_media_delivery.py'
Result "tests_diffusion_non_regression" (AllPassed $dlT 27) "$($dlT.Count) tests"
$edT = FileTests 'test_editorial.py'
Result "tests_editoriaux_non_regression" (AllPassed $edT 20) "$($edT.Count) tests"
Result "pytest_e1_sans_echec" ($pyCode -eq 0 -and @($lines | Where-Object { $_ -notmatch 'PASSED' }).Count -eq 0) "code=$pyCode ; $($lines.Count) tests"

# -----------------------------------------------------------------------------
# 6. Intégrité et garde-fous
# -----------------------------------------------------------------------------
Log ""; Log ">>> Intégrité"
$volsAfter = @(docker volume ls --format "{{.Name}}" 2>$null | Where-Object { $_ -like "nurselearn*" })
$lostVols = @($volsBefore | Where-Object { $_ -notin $volsAfter })
Result "aucun_volume_supprime" ($lostVols.Count -eq 0) "avant=$($volsBefore.Count) après=$($volsAfter.Count) disparus=$($lostVols -join ',')"
$countsAfter = Psql $COUNTS
$b = "$countsBefore" -split ' '; $a = "$countsAfter" -split ' '
$lost = 0; for ($i = 0; $i -lt $b.Count; $i++) { if ([int64]$a[$i] -lt [int64]$b[$i]) { $lost++ } }
Result "aucune_donnee_perdue" ($a.Count -eq 10 -and $lost -eq 0) "avant=[$countsBefore] après=[$countsAfter]"
$invAfter = Json (Ops "--inventory")
$objLost = 0
if ($invBefore -and $invAfter) { foreach ($p in $invBefore.PSObject.Properties) { if ($p.Name -ne 'ok' -and [int]$invAfter.($p.Name) -lt [int]$p.Value) { $objLost++ } } } else { $objLost = -1 }
Result "aucun_objet_s3_perdu" ($objLost -eq 0) "avant=$($invBefore | ConvertTo-Json -Compress) après=$($invAfter | ConvertTo-Json -Compress)"
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"
$pilot = Psql "SELECT status FROM programs WHERE code = 'pab-qc-socle'"
Result "pilote_pab_en_brouillon" ($pilot -eq "draft") $pilot
$presign = (git grep -n -E "generate_presigned|presigned_url|PublicRead|public-read" -- services apps 2>$null | Where-Object { $_ -notmatch 'tests/' }) -join " "
Result "aucune_url_publique_ni_presignee" ([string]::IsNullOrWhiteSpace($presign)) $presign

Log ""; Log "================ SYNTHÈSE 2.2.3-e ($Stage) ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.3-e {2} (+ non-régression 2.2.3-d, R3, D14, 2.2.3-c, 2.2.3-b, 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 1.2), {1} échec(s)" -f $results.Count, $fail, $Stage)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

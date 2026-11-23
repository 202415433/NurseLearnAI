# =============================================================================
# NurseLearn AI — Validation du correctif R3 (séparation des identités S3 / RustFS)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche security-r3-s3-identity-separation) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-r3-s3-identities.ps1
#
# 0. état Git ; secrets des trois identités S3 présents dans .env (générés s'ils manquent, jamais affichés) ;
# 1. INVENTAIRE avant toute modification : volumes Docker, compteurs PostgreSQL, objets S3 ;
# 2. non-régression complète : scripts\validate-d14-postgres-roles.ps1 (pile reconstruite : `s3-init`
#    provisionne les identités ; D14, 2.2.3-c ClamAV/EICAR, 2.2.3-b … 1.2) ;
# 3. contrôles R3 : identités et politiques RustFS, identifiants réellement remis à chaque conteneur,
#    sondes adversariales DANS l'API et le worker, tests adversariaux pytest sur le RustFS de la pile,
#    accès anonyme, redémarrage de RustFS (persistance), journaux sans secret ;
# 4. intégrité : aucun volume, aucune donnée, aucun objet perdu ; aucune publication médicale.
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\r3-<date>.log
# =============================================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# Sans BOM : la sonde R3 est transmise à Python par l'entrée standard des conteneurs.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$root = (Get-Location).Path
if (-not (Test-Path "$root\compose.yaml")) { Write-Error "Lancer depuis la racine du dépôt (C:\NurseLearnAI-git)."; exit 2 }
if (-not (Test-Path "$root\.env")) { Write-Error ".env absent."; exit 2 }
$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "r3-$stamp.log"
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

Log "NurseLearn AI — validation R3 (identités S3 / RustFS) — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_r3" ($branch -like "security-r3*" -or $branch -like "phase-2.2.3[d-z]*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
$hist = (git diff --diff-filter=MD --name-only origin/main...HEAD -- database/schema services/api/alembic/versions 2>$null) -join " "
Result "migrations_historiques_inchangees" ([string]::IsNullOrWhiteSpace($hist)) $hist
$newMig = (git diff --diff-filter=A --name-only origin/main...HEAD -- database/schema services/api/alembic/versions 2>$null) -join " "
Result "aucune_nouvelle_migration" ([string]::IsNullOrWhiteSpace($newMig)) $newMig
Result "fichiers_r3_presents" ((Test-Path "$root\services\api\app\ops\s3_provision.py") -and (Test-Path "$root\services\api\tests\api\test_s3_identities.py") -and (Test-Path "$root\scripts\r3_s3_probe.py")) ""

# -----------------------------------------------------------------------------
# 0. Secrets des identités S3 (fichier .env local, jamais versionné)
# -----------------------------------------------------------------------------
$envVars = ReadEnv
foreach ($k in 'S3_API_SECRET_KEY', 'S3_SCANNER_SECRET_KEY', 'S3_READER_SECRET_KEY', 'API_DB_PASSWORD', 'MEDIA_SCANNER_DB_PASSWORD') {
    if (-not $envVars[$k]) {
        # Clés secrètes S3 : 32 caractères hexadécimaux (limite S3 de 40) ; mots de passe PostgreSQL : 48.
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
$s3k = @($envVars['S3_API_SECRET_KEY'], $envVars['S3_SCANNER_SECRET_KEY'], $envVars['S3_READER_SECRET_KEY'])
$valid = @($s3k | Where-Object { $_ -match '^[A-Za-z0-9]{32,40}$' }).Count
$distinct = @(($s3k + $envVars['S3_SECRET_KEY']) | Sort-Object -Unique).Count
Result "secrets_s3_presents_distincts" ($valid -eq 3 -and $distinct -eq 4) "valides=$valid/3, distincts (avec la clé racine)=$distinct/4"
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
$PGPW = $envVars['POSTGRES_PASSWORD']
$S3AK = if ($envVars['S3_ACCESS_KEY']) { $envVars['S3_ACCESS_KEY'] } else { "nurselearn" }
function Psql([string]$sql, [string]$db = $PGDB) { (docker compose exec -T postgres psql -U $PGU -d $db -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }
function Ops([string]$opt) { (docker compose run --rm --no-deps -T s3-init python -m app.ops.s3_provision $opt 2>&1 | ForEach-Object { "$_" }) -join "`n" }
$COUNTS = "SELECT (SELECT count(*) FROM users) || ' ' || (SELECT count(*) FROM user_roles) || ' ' || (SELECT count(*) FROM organizations) || ' ' || (SELECT count(*) FROM programs) || ' ' || (SELECT count(*) FROM lesson_versions) || ' ' || (SELECT count(*) FROM program_releases) || ' ' || (SELECT count(*) FROM media_assets) || ' ' || (SELECT count(*) FROM media_scan_events) || ' ' || (SELECT count(*) FROM editorial_decisions) || ' ' || (SELECT count(*) FROM audit_logs)"

# -----------------------------------------------------------------------------
# 1. Inventaire AVANT modification (volumes, base, objets S3)
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
# 2. Non-régression complète : D14 → 2.2.3-c (ClamAV, EICAR) → 2.2.3-b … 1.2
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-d14-postgres-roles.ps1 (D14, 2.2.3-c, 2.2.3-b … 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-d14-postgres-roles.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\d14-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_d14_et_anterieures" $false "aucun nouveau journal"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles D14.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_d14_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
    foreach ($l in (Select-String -Path $reg.FullName -Pattern "non_regression_2_2_3c|pytest_complet|sonde_adversariale_api" | ForEach-Object { $_.Line.Trim() })) { Log "    $l" }
}
$reg3c = Get-ChildItem "$logDir\phase2.2.3c-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if ($reg3c) {
    foreach ($n in 'tests_worker_clamav_reel', 'eicar_detecte_par_clamav_reel') {
        $l = Select-String -Path $reg3c.FullName -Pattern "=> \[(PASS|FAIL)\] $n" | Select-Object -Last 1
        Result "clamav_$n" ("$($l.Line)" -match "PASS") "journal $($reg3c.Name)"
    }
}

# -----------------------------------------------------------------------------
# 3. Contrôles R3
# -----------------------------------------------------------------------------
Log ""; Log ">>> Identités S3 et politiques RustFS"
docker compose --profile media up -d 2>&1 | Out-Null
$init = (docker compose ps -a s3-init --format "{{.ExitCode}}" 2>$null | Select-Object -First 1)
Result "provisionnement_s3_init_termine" ("$init".Trim() -eq "0") "code de sortie=$init"
$chk = Json (Ops "--check")
Result "identites_actives_politique_propre" ($chk -and $chk.ok -and $chk.'nl-api-uploader'.policy -eq 'nl-api-uploader' -and $chk.'nl-media-scanner'.policy -eq 'nl-media-scanner' -and $chk.'nl-media-reader'.policy -eq 'nl-media-reader') $(if ($chk) { ($chk | ConvertTo-Json -Compress) } else { "réponse illisible" })

# Identifiants réellement remis à chaque conteneur (valeurs comparées, jamais affichées).
function EnvOf([string]$svc, [string]$name) { (docker compose --profile media exec -T $svc printenv $name 2>$null | Out-String).Trim() }
$apiAk = EnvOf api S3_ACCESS_KEY; $apiSk = EnvOf api S3_SECRET_KEY
$wAk = EnvOf media-worker S3_ACCESS_KEY; $wSk = EnvOf media-worker S3_SECRET_KEY
Result "api_identite_televersement" ($apiAk -eq "nl-api-uploader" -and $apiSk -eq $envVars['S3_API_SECRET_KEY']) "identité=$apiAk"
Result "worker_identite_analyse" ($wAk -eq "nl-media-scanner" -and $wSk -eq $envVars['S3_SCANNER_SECRET_KEY']) "identité=$wAk"
$rootSk = $envVars['S3_SECRET_KEY']
$rootHolders = @()
foreach ($svc in 'api', 'web', 'migrate', 'media-worker', 'media-delivery', 'clamd', 'freshclam', 'postgres', 'redis', 'mailpit') {
    $cid = docker compose --profile media ps -a -q $svc 2>$null | Select-Object -First 1
    if (-not $cid) { continue }
    $envDump = (docker inspect -f "{{range .Config.Env}}{{println .}}{{end}}" $cid 2>$null | Out-String)
    if ($rootSk -and $envDump.Contains($rootSk)) { $rootHolders += $svc }
    if ($svc -in 'api', 'media-worker', 'web' -and ($envDump -match 'S3_ADMIN_|S3_READER_SECRET_KEY|S3_API_SECRET_KEY|S3_SCANNER_SECRET_KEY' -or ($envVars['S3_READER_SECRET_KEY'] -and $envDump.Contains($envVars['S3_READER_SECRET_KEY'])))) { $rootHolders += "$svc(secret étranger)" }
}
Result "cle_racine_reservee_a_s3_et_s3_init" ($rootHolders.Count -eq 0) "détenteurs hors s3/s3-init : $($rootHolders -join ',')"
$webS3 = (docker compose exec -T web sh -c 'env | grep -c "^S3_\|^NEXT_PUBLIC_S3"' 2>$null | Out-String).Trim()
Result "aucune_cle_s3_dans_le_frontend" ($webS3 -eq "0" -or $webS3 -eq "") "variables S3 dans web=$webS3"

Log ""; Log ">>> Sondes adversariales DANS les conteneurs (identifiants propres à chaque service)"
# Sonde transmise par l'entrée standard : le worker a un système de fichiers en lecture seule et un
# /tmp en tmpfs, où `docker compose cp` ne dépose rien de visible.
$probeSrc = Get-Content "$root\scripts\r3_s3_probe.py" -Raw -Encoding UTF8
function Probe([string]$svc, [string]$role) { ($probeSrc | docker compose --profile media exec -T $svc python - $role 2>&1 | Out-String) }
$pa = Json (Probe api api)
Result "sonde_api_tout_refuse" ($pa -and $pa.ok -and $pa.identity -eq "nl-api-uploader") $(if ($pa) { "$($pa.refused)/$($pa.total) refusées, inattendues : $($pa.unexpected -join ',')" } else { "sonde illisible" })
$pwRaw = Probe media-worker scanner
$pw = Json $pwRaw
if (-not $pw) { Log "    sortie de la sonde worker : $(($pwRaw -split "`n" | Select-Object -Last 5) -join ' | ')" }
Result "sonde_worker_minimale" ($pw -and $pw.ok -and $pw.identity -eq "nl-media-scanner") $(if ($pw) { "$($pw.refused)/$($pw.total) refusées, permises : $($pw.allowed_ok -join ',') ; inattendues : $($pw.unexpected -join ',')" } else { "sonde illisible" })
$sc = (docker compose --profile media exec -T media-worker python -m app.workers.media_scan --self-check 2>&1 | Out-String)
Result "worker_auto_verification" ($LASTEXITCODE -eq 0) (($sc -split "`n" | Select-Object -Last 1).Trim())

foreach ($b in 'nl-media-quarantine', 'nl-media-clean') {
    $codes = @()
    foreach ($m in 'GET', 'PUT', 'DELETE') {
        $u = if ($m -eq 'GET') { "http://127.0.0.1:9000/$b/" } else { "http://127.0.0.1:9000/$b/r3-anonyme-$(NewSecret 4)" }
        $req = @{ UseBasicParsing = $true; Method = $m; Uri = $u; ErrorAction = 'Stop' }
        if ($m -eq 'PUT') { $req['Body'] = 'x' }
        try { $r = Invoke-WebRequest @req; $codes += [int]$r.StatusCode } catch { $codes += $(if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { -1 }) }
    }
    Result "anonyme_refuse_$b" (@($codes | Where-Object { $_ -ne 403 }).Count -eq 0) "GET/PUT/DELETE = $($codes -join '/')"
}

Log ""; Log ">>> pytest : tests adversariaux R3 sur le RustFS de la pile (identités de test nl-test-*)"
Psql "DROP DATABASE IF EXISTS nl_validation_r3 WITH (FORCE)" "postgres" | Out-Null
Psql "CREATE DATABASE nl_validation_r3" "postgres" | Out-Null
$cid = (docker create -v "${root}:/repo" -w /repo --network nurselearn_default `
    -e "DATABASE_URL=postgresql+asyncpg://${PGU}:${PGPW}@postgres:5432/nl_validation_r3" `
    -e "REDIS_URL=redis://:$($envVars['REDIS_PASSWORD'])@redis:6379/0" `
    -e NL_TEST_S3_ENDPOINT=http://s3:9000 -e NL_TEST_S3_KIND=rustfs -e "S3_ACCESS_KEY=$S3AK" -e "S3_SECRET_KEY=$rootSk" `
    -e "API_DB_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_API_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_ADMIN_DATABASE_URL=postgresql+psycopg://${PGU}:${PGPW}@postgres:5432/nl_validation_r3" `
    -e NL_TEST_PGHOST=postgres -e NL_TEST_PGPORT=5432 -e NL_TEST_PGUSER=$PGU -e PGPASSWORD=$PGPW -e PYTHONDONTWRITEBYTECODE=1 `
    python:3.12-slim sh -c "cd services/api && pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]' && alembic upgrade head && python -m app.seed && DATABASE_URL=postgresql+asyncpg://nl_api:$($envVars['API_DB_PASSWORD'])@postgres:5432/nl_validation_r3 python -m pytest -v -rs -p no:cacheprovider tests/api/test_s3_identities.py" 2>&1).Trim()
$pyOut = (docker start -a $cid 2>&1 | Out-String)
$pyCode = [int](docker inspect -f "{{.State.ExitCode}}" $cid)
docker rm $cid 2>&1 | Out-Null
Psql "DROP DATABASE IF EXISTS nl_validation_r3 WITH (FORCE)" "postgres" | Out-Null
foreach ($l in ($pyOut -split "`n" | Where-Object { $_ -match 'test_s3_identities.py::|passed|failed|skipped|error' } | Select-Object -Last 20)) { Log "    $($l.Trim())" }
$passed = if ($pyOut -match '(\d+) passed') { [int]$Matches[1] } else { 0 }
Result "tests_adversariaux_r3_rustfs_reel" ($pyCode -eq 0 -and $passed -ge 9 -and $pyOut -notmatch '\d+ (skipped|failed)') "$passed tests réussis"

Log ""; Log ">>> Redémarrage de RustFS : persistance des identités et politiques"
docker compose restart s3 2>&1 | Out-Null
for ($i = 0; $i -lt 30; $i++) { $h = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q s3) 2>$null; if ($h -eq "healthy") { break }; Start-Sleep -Seconds 2 }
$chk2 = Json (Ops "--check")
$pa2 = Json (Probe api api)
$pw2 = Json (Probe media-worker scanner)
Result "politiques_persistantes_apres_redemarrage" ($chk2 -and $chk2.ok -and $pa2 -and $pa2.ok -and $pw2 -and $pw2.ok) "check=$($chk2.ok) api=$($pa2.ok) worker=$($pw2.ok)"

Log ""; Log ">>> Services Docker"
$bad = @()
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web', 'freshclam', 'clamd', 'media-worker') {
    $h = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q $s) 2>$null
    if ($h -ne "healthy") { $bad += "$s=$h" }
}
Result "services_docker_sains" ($bad.Count -eq 0) $(if ($bad) { $bad -join ' ' } else { "8/8 sains ; migrate et s3-init terminés" })
$ports = (docker compose port s3 9000 2>$null | Out-String).Trim() + " " + (docker compose port s3 9001 2>$null | Out-String).Trim()
Result "rustfs_et_console_sur_boucle_locale" ($ports -match '^127\.0\.0\.1:9000 127\.0\.0\.1:9001$') $ports
$alllogs = (docker compose --profile media logs --no-color 2>&1 | Out-String)
$leaks = 0; foreach ($k in 'S3_SECRET_KEY', 'S3_API_SECRET_KEY', 'S3_SCANNER_SECRET_KEY', 'S3_READER_SECRET_KEY', 'POSTGRES_PASSWORD', 'API_DB_PASSWORD', 'MEDIA_SCANNER_DB_PASSWORD') { $v = $envVars[$k]; if ($v -and $alllogs.Contains($v)) { $leaks++ } }
Result "journaux_sans_secret" ($leaks -eq 0) "secrets retrouvés dans les journaux = $leaks"

# -----------------------------------------------------------------------------
# 4. Intégrité et garde-fous
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
$routes = (docker compose exec -T api python -c "from app.main import create_app; p = create_app().openapi()['paths']; print(sorted(f'{m.upper()} {k}' for k, v in p.items() if k.startswith('/api/v1/editorial/media') for m in v))" 2>&1 | Out-String).Trim()
Result "aucune_route_de_diffusion" ($routes -eq "['DELETE /api/v1/editorial/media/{media_id}', 'GET /api/v1/editorial/media', 'GET /api/v1/editorial/media/{media_id}', 'POST /api/v1/editorial/media', 'POST /api/v1/editorial/media/{media_id}/revoke', 'PUT /api/v1/editorial/media/{media_id}/content']") $routes
$presign = (git grep -n -E "generate_presigned|presigned_url|PublicRead|public-read" -- services apps 2>$null | Where-Object { $_ -notmatch 'tests/' }) -join " "
Result "aucune_url_publique_ni_presignee" ([string]::IsNullOrWhiteSpace($presign)) $presign

Log ""; Log "================ SYNTHÈSE R3 ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles R3 (+ non-régression D14, 2.2.3-c, 2.2.3-b, 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

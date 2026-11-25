# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.3-d (diffusion sécurisée des médias)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.3d-secure-media-delivery) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3d.ps1
#
# 0. état Git ; secrets présents dans .env (générés s'ils manquent, jamais affichés) ;
# 1. INVENTAIRE avant toute modification : volumes Docker, compteurs PostgreSQL, objets S3 ;
# 2. non-régression complète : scripts\validate-r3-s3-identities.ps1 (R3 → D14 → 2.2.3-c ClamAV/EICAR
#    → 2.2.3-b … 1.2 ; pile reconstruite, dont le nouveau service `media-delivery`) ;
# 3. contrôles 2.2.3-d : service de diffusion séparé et sain, identité PostgreSQL (nl_api), identité S3
#    de LECTURE (nl-media-reader), signatures ClamAV fraîches, aucune diffusion par l'API d'édition,
#    accès anonyme refusé, tests de diffusion / Range / révocation / adversariaux sur la pile RÉELLE
#    (PostgreSQL, Redis, RustFS, ClamAV), rapport de conservation en lecture seule, journaux sans secret ;
# 4. intégrité : aucun volume, aucune donnée, aucun objet perdu ; aucune publication médicale.
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\phase2.2.3d-<date>.log
# =============================================================================
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
$log = Join-Path $logDir "phase2.2.3d-$stamp.log"
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

Log "NurseLearn AI — validation 2.2.3-d (diffusion sécurisée des médias) — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_2_2_3d" ($branch -like "phase-2.2.3[d-z]*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
# 2.2.3-d n'ajoute aucune migration ; une étape ultérieure (0010, 2.2.3-e) peut en AJOUTER, jamais en modifier.
$mig = (git diff --name-only $(if ($branch -like "phase-2.2.3d*") { "--diff-filter=ACMRD" } else { "--diff-filter=MD" }) origin/main...HEAD -- database/schema services/api/alembic/versions 2>$null) -join " "
Result "aucune_migration_modifiee_ni_ajoutee" ([string]::IsNullOrWhiteSpace($mig)) $mig
Result "fichiers_2_2_3d_presents" ((Test-Path "$root\services\api\app\domains\media\delivery.py") -and (Test-Path "$root\services\api\tests\api\test_media_delivery.py") -and (Test-Path "$root\services\api\app\ops\media_retention.py")) ""

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
# 2. Non-régression complète : R3 → D14 → 2.2.3-c → … → 1.2
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-r3-s3-identities.ps1 (R3, D14, 2.2.3-c, 2.2.3-b … 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-r3-s3-identities.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\r3-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_r3_d14_et_anterieures" $false "aucun nouveau journal"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles R3.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_r3_d14_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
    foreach ($l in (Select-String -Path $reg.FullName -Pattern "non_regression_d14|clamav_|tests_adversariaux_r3|politiques_persistantes" | ForEach-Object { $_.Line.Trim() })) { Log "    $l" }
}

# -----------------------------------------------------------------------------
# 3. Contrôles 2.2.3-d
# -----------------------------------------------------------------------------
Log ""; Log ">>> Services Docker et service de diffusion"
docker compose --profile media up -d 2>&1 | Out-Null
for ($i = 0; $i -lt 40; $i++) { $h = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q media-delivery) 2>$null; if ($h -eq "healthy") { break }; Start-Sleep -Seconds 3 }
$bad = @()
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web', 'media-delivery', 'freshclam', 'clamd', 'media-worker') {
    $h = docker inspect -f "{{.State.Health.Status}}" (docker compose --profile media ps -q $s) 2>$null
    if ($h -ne "healthy") { $bad += "$s=$h" }
}
Result "services_docker_sains" ($bad.Count -eq 0) $(if ($bad) { $bad -join ' ' } else { "9/9 sains (dont media-delivery) ; migrate et s3-init terminés" })
$port = (docker compose port media-delivery 8000 2>$null | Out-String).Trim()
Result "diffusion_sur_boucle_locale" ($port -eq "127.0.0.1:$dPort") $port

function EnvOf([string]$svc, [string]$name) { (docker compose --profile media exec -T $svc printenv $name 2>$null | Out-String).Trim() }
$dAk = EnvOf media-delivery S3_ACCESS_KEY; $dSk = EnvOf media-delivery S3_SECRET_KEY; $dDb = EnvOf media-delivery DATABASE_URL
Result "diffusion_identite_s3_lecture" ($dAk -eq "nl-media-reader" -and $dSk -eq $envVars['S3_READER_SECRET_KEY']) "identité=$dAk"
$cid = docker compose ps -q media-delivery | Select-Object -First 1
$dEnv = (docker inspect -f "{{range .Config.Env}}{{println .}}{{end}}" $cid 2>$null | Out-String)
$foreign = @()
foreach ($k in 'S3_SECRET_KEY', 'S3_API_SECRET_KEY', 'S3_SCANNER_SECRET_KEY', 'POSTGRES_PASSWORD', 'MEDIA_SCANNER_DB_PASSWORD') { $v = $envVars[$k]; if ($v -and $dEnv.Contains($v)) { $foreign += $k } }
Result "diffusion_sans_cle_racine_ni_autre_identite" ($foreign.Count -eq 0 -and $dEnv -notmatch 'MEDIA_QUARANTINE_BUCKET') "secrets étrangers : $($foreign -join ',')"
$apiSk = EnvOf api S3_SECRET_KEY
Result "api_sans_identite_de_lecture" ($apiSk -ne $envVars['S3_READER_SECRET_KEY'] -and (EnvOf api S3_ACCESS_KEY) -eq "nl-api-uploader") ""

Log ""; Log ">>> Identités PostgreSQL et RustFS"
$dbProbe = @"
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    print(await c.fetchval('SELECT current_user'), await c.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname = current_user'))
asyncio.run(m())
"@
$dbUser = (($dbProbe | docker compose exec -T media-delivery python - 2>&1) | Out-String).Trim()
Result "diffusion_connectee_en_nl_api" ($dDb -match '^postgresql\+asyncpg://nl_api:' -and $dbUser -eq "nl_api False") "connexion=$dbUser"
$chk = Json (Ops "--check")
Result "identites_s3_actives" ($chk -and $chk.ok -and $chk.'nl-media-reader'.policy -eq 'nl-media-reader') $(if ($chk) { ($chk | ConvertTo-Json -Compress) } else { "réponse illisible" })
$probeSrc = Get-Content "$root\scripts\r3_s3_probe.py" -Raw -Encoding UTF8
$pr = Json (($probeSrc | docker compose exec -T media-delivery python - reader 2>&1 | Out-String))
Result "sonde_lecture_seule_dans_la_diffusion" ($pr -and $pr.ok -and $pr.identity -eq "nl-media-reader") $(if ($pr) { "$($pr.refused)/$($pr.total) refusées, permises : $($pr.allowed_ok -join ',') ; inattendues : $($pr.unexpected -join ',')" } else { "sonde illisible" })

Log ""; Log ">>> Signatures ClamAV"
$sc = (docker compose --profile media exec -T media-worker python -m app.workers.media_scan --self-check 2>&1 | Out-String)
$scOk = ($LASTEXITCODE -eq 0)
Result "signatures_clamav_fraiches" $scOk (($sc -split "`n" | Where-Object { $_ -match '\{' } | Select-Object -Last 1) -replace '\s+', ' ')

Log ""; Log ">>> Points d'accès"
$apiPaths = (docker compose exec -T api python -c "from app.main import create_app; p = create_app().openapi()['paths']; print(sum(1 for k in p if k.startswith('/api/v1/media')), sorted(f'{m.upper()} {k}' for k, v in p.items() if k.endswith('/revoke') for m in v))" 2>&1 | Out-String).Trim()
Result "api_edition_ne_diffuse_rien" ($apiPaths -eq "0 ['POST /api/v1/editorial/media/{media_id}/revoke']") $apiPaths
$dPaths = (docker compose exec -T media-delivery python -c "from app.main import create_delivery_app; p = create_delivery_app().openapi()['paths']; print(sorted(f'{m.upper()} {k}' for k, v in p.items() if k.startswith('/api/v1/media') for m in v))" 2>&1 | Out-String).Trim()
Result "diffusion_lecture_seule_deux_routes" ($dPaths -eq "['GET /api/v1/media/{media_id}/content', 'GET /api/v1/media/{media_id}/preview']") $dPaths
$z = "00000000-0000-4000-a000-000000000000"
$c1 = HttpCode "http://127.0.0.1:$dPort/api/v1/media/$z/preview"
$c2 = HttpCode "http://127.0.0.1:$dPort/api/v1/media/$z/content?release=$z&lesson_version=$z" @{ Authorization = "Bearer inconnu" }
$c3 = HttpCode "http://127.0.0.1:9000/nl-media-clean/"
Result "acces_anonyme_refuse" ($c1 -eq 401 -and $c2 -eq 401 -and $c3 -eq 403) "diffusion sans session=$c1, session inconnue=$c2, S3 anonyme=$c3"

Log ""; Log ">>> pytest : diffusion, Range, révocation, adversariaux (PostgreSQL, Redis, RustFS, ClamAV réels)"
Psql "DROP DATABASE IF EXISTS nl_validation_d3 WITH (FORCE)" "postgres" | Out-Null
Psql "CREATE DATABASE nl_validation_d3" "postgres" | Out-Null
$cid = (docker create -v "${root}:/repo" -w /repo --network nurselearn_default `
    -e "DATABASE_URL=postgresql+asyncpg://${PGU}:${PGPW}@postgres:5432/nl_validation_d3" `
    -e "REDIS_URL=redis://:$($envVars['REDIS_PASSWORD'])@redis:6379/0" `
    -e NL_TEST_S3_ENDPOINT=http://s3:9000 -e NL_TEST_S3_KIND=rustfs -e "S3_ACCESS_KEY=$S3AK" -e "S3_SECRET_KEY=$rootSk" `
    -e NL_TEST_CLAMD_HOST=clamd -e NL_TEST_CLAMD_PORT=3310 -e "NL_TEST_SCANNER_PASSWORD=$($envVars['MEDIA_SCANNER_DB_PASSWORD'])" `
    -e "API_DB_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_API_PASSWORD=$($envVars['API_DB_PASSWORD'])" -e "NL_TEST_ADMIN_DATABASE_URL=postgresql+psycopg://${PGU}:${PGPW}@postgres:5432/nl_validation_d3" `
    -e NL_TEST_PGHOST=postgres -e NL_TEST_PGPORT=5432 -e NL_TEST_PGUSER=$PGU -e PGPASSWORD=$PGPW -e PYTHONDONTWRITEBYTECODE=1 `
    python:3.12-slim sh -c "cd services/api && pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]' && alembic upgrade head && python -m app.seed && DATABASE_URL=postgresql+asyncpg://nl_api:$($envVars['API_DB_PASSWORD'])@postgres:5432/nl_validation_d3 python -m pytest -v -rfEs -p no:cacheprovider -p no:logging tests/api/test_media_delivery.py tests/api/test_media_scan.py tests/api/test_media_upload.py tests/api/test_s3_identities.py" 2>&1).Trim()
docker network connect nurselearn_media-scan $cid 2>&1 | Out-Null
$pyOut = (docker start -a $cid 2>&1 | Out-String)
$pyCode = [int](docker inspect -f "{{.State.ExitCode}}" $cid)
docker rm $cid 2>&1 | Out-Null
Psql "DROP DATABASE IF EXISTS nl_validation_d3 WITH (FORCE)" "postgres" | Out-Null
$lines = @($pyOut -split "`n" | Where-Object { $_ -match '::test_\S+ (PASSED|FAILED|SKIPPED|ERROR)' } | ForEach-Object { $_.Trim() })
foreach ($l in ($lines | Where-Object { $_ -match 'test_media_delivery' })) { Log "    $l" }
foreach ($l in ($pyOut -split "`n" | Where-Object { $_ -match '^=+ .*(passed|failed|error).* =+$' })) { Log "    $($l.Trim())" }
# Cause d'un échec (résumé pytest et premières lignes d'assertion), sans secret : diagnostic seulement.
foreach ($l in ($pyOut -split "`n" | Where-Object { $_ -match '^(FAILED|ERROR) \S+ - ' } | Select-Object -First 5)) { Log "      $($l.Trim())" }
foreach ($l in ($pyOut -split "`n" | Where-Object { $_ -match '^E\s{2,}' } | Select-Object -First 12)) { Log "      $($l.TrimEnd())" }
# Nom distinct de « group » (alias PowerShell de Group-Object, prioritaire sur les fonctions).
function TestsLike([string]$pattern) { @($lines | Where-Object { $_ -match 'test_media_delivery' -and $_ -match $pattern }) }
$dl = TestsLike '.'
$dlBad = @($dl | Where-Object { $_ -notmatch 'PASSED' })
Result "tests_diffusion_sans_echec_ni_saut" ($pyCode -eq 0 -and $dl.Count -ge 25 -and $dlBad.Count -eq 0) "$($dl.Count) tests ; non réussis : $($dlBad -join ' | ')"
$rg = TestsLike 'range'
Result "tests_http_range" ($rg.Count -ge 4 -and @($rg | Where-Object { $_ -notmatch 'PASSED' }).Count -eq 0) "$($rg.Count) tests Range réussis"
$rv = TestsLike 'revoke|revoked|revocation|session|role_change|disabled|cut_when'
Result "tests_revocation" ($rv.Count -ge 4 -and @($rv | Where-Object { $_ -notmatch 'PASSED' }).Count -eq 0) "$($rv.Count) tests de révocation réussis"
$adv = TestsLike 'never|refused|isolation|identifier|direct|read_only|incoherent|failure|rate_limit|concurrent|legal_hold|frozen|medical'
Result "tests_adversariaux" ($adv.Count -ge 12 -and @($adv | Where-Object { $_ -notmatch 'PASSED' }).Count -eq 0) "$($adv.Count) tests adversariaux réussis"
$others = @($lines | Where-Object { $_ -notmatch 'test_media_delivery' })
$othersBad = @($others | Where-Object { $_ -notmatch 'PASSED' })
Result "non_regression_televersement_analyse_identites" ($others.Count -ge 40 -and $othersBad.Count -eq 0) "$($others.Count) tests (téléversement, ClamAV/EICAR, identités S3) ; non réussis : $($othersBad -join ' | ')"

Log ""; Log ">>> Conservation (rapport en lecture seule)"
$ret = Json ((docker compose exec -T api python -m app.ops.media_retention 2>&1 | Out-String))
Result "rapport_conservation_lecture_seule" ($ret -and "$($ret.purge_automatique)" -like "desactivee*") $(if ($ret) { "conservation légale=$($ret.conservation_legale) ; rattachés à une version figée=$($ret.rattaches_a_une_version_figee) ; déjà purgés=$($ret.deja_purges)" } else { "rapport illisible" })

$alllogs = (docker compose --profile media logs --no-color 2>&1 | Out-String)
$leaks = 0; foreach ($k in 'S3_SECRET_KEY', 'S3_API_SECRET_KEY', 'S3_SCANNER_SECRET_KEY', 'S3_READER_SECRET_KEY', 'POSTGRES_PASSWORD', 'API_DB_PASSWORD', 'MEDIA_SCANNER_DB_PASSWORD') { $v = $envVars[$k]; if ($v -and $alllogs.Contains($v)) { $leaks++ } }
$dlogs = (docker compose logs --no-color media-delivery 2>&1 | Out-String)
Result "journaux_sans_secret_ni_url_de_stockage" ($leaks -eq 0 -and $dlogs -notmatch 'X-Amz-Signature|http://s3:9000/nl-media') "secrets retrouvés = $leaks"

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
$presign = (git grep -n -E "generate_presigned|presigned_url|PublicRead|public-read" -- services apps 2>$null | Where-Object { $_ -notmatch 'tests/' }) -join " "
Result "aucune_url_publique_ni_presignee" ([string]::IsNullOrWhiteSpace($presign)) $presign

Log ""; Log "================ SYNTHÈSE 2.2.3-d ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.3-d (+ non-régression R3, D14, 2.2.3-c, 2.2.3-b, 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

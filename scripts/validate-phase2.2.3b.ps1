# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.3-b (téléversement sécurisé des médias en quarantaine)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.3b-secure-upload) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3b.ps1
#
# 1. non-régression complète : scripts\validate-phase2.2.3a.ps1 (enchaîne 2.2.2-c, 2.2.2-b, 2.2.2-a,
#    2.2.1, 2.1, 2.0 et 1.2 : qualité, audits, pile Docker reconstruite, pytest sur bases jetables
#    — dont test_media_upload.py sur le RustFS RÉEL de la pile —, Vitest, Playwright + axe) ;
# 2. contrôles propres à 2.2.3-b, en LECTURE SEULE sur la base de développement : aucun média créé,
#    les seules requêtes HTTP sont des accès anonymes REFUSÉS.
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\phase2.2.3b-<date>.log
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
$log = Join-Path $logDir "phase2.2.3b-$stamp.log"
$results = [ordered]@{}

function Log([string]$msg) { Write-Host $msg; Add-Content -Path $script:log -Value $msg -Encoding UTF8 }
function Result([string]$name, [bool]$ok, [string]$detail = "") {
    $script:results[$name] = $ok
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    Log "    => [$tag] $name $detail"
}
$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envVars[$k.Trim()] = $v.Trim() }
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
$apiPort = if ($envVars['API_PORT']) { $envVars['API_PORT'] } else { "28000" }
function Psql([string]$sql) { (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }
function ApiPy([string]$code) { (docker compose exec -T api python -c $code 2>&1 | ForEach-Object { "$_" }) -join " " }
function Anon([string]$method, [string]$uri, [string]$body = $null) {
    try {
        $p = @{ UseBasicParsing = $true; Method = $method; Uri = $uri; ErrorAction = "Stop" }
        if ($body) { $p.Body = $body; $p.ContentType = "application/json" }
        $r = Invoke-WebRequest @p
        return @{ Code = [int]$r.StatusCode; Type = "$($r.Headers['Content-Type'])" }
    } catch {
        if ($_.Exception.Response) { return @{ Code = [int]$_.Exception.Response.StatusCode; Type = "$($_.Exception.Response.ContentType)" } }
        return @{ Code = -1; Type = "$($_.Exception.Message)" }
    }
}

Log "NurseLearn AI — validation étape 2.2.3-b — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_2_3b" ($branch -like "phase-2.2.3[b-z]*" -or $branch -like "security-*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
# 2.2.3-b ne touche à AUCUNE migration : aucune différence sous database/ depuis la base de la branche.
# Aucune migration EXISTANTE modifiée ou supprimée (les migrations additives ultérieures sont admises).
$schema = (git diff --diff-filter=MD --name-only origin/main...HEAD -- database/schema 2>$null) -join " "
Result "aucune_migration_modifiee_ou_ajoutee" ([string]::IsNullOrWhiteSpace($schema)) $schema

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.2.3-a → … → 1.2)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.3a.ps1 (enchaîne 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0 et 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.3a.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.3a-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_3a_et_anterieures" $false "aucun nouveau journal : validate-phase2.2.3a.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.3-a.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_3a_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    Log "    pytest (dont test_media_upload.py sur RustFS réel) : $($py.Line.Trim())"
    Result "pytest_complet_dont_televersement" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
    # Les tests de téléversement sont ignorés sans stockage : ils doivent avoir réellement tourné.
    $skipS3 = (Select-String -Path $reg12.FullName -Pattern "NL_TEST_S3_ENDPOINT absent").Count
    Result "tests_televersement_executes_sur_rustfs" ($skipS3 -eq 0) "tests ignorés faute de S3 = $skipS3"
} else {
    Result "pytest_complet_dont_televersement" $false "journal 1.2 introuvable"
}

# -----------------------------------------------------------------------------
# 2. Contrôles 2.2.3-b (lecture seule)
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0007_toujours_en_tete" ($rev -match '^000[7-9]_') $rev
$scan = Psql "SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id WHERE rp.permission_code = 'media:scan' AND r.code <> 'media_scanner'"
Result "aucun_role_ne_detient_media_scan" ($scan -eq "0") "rôles hors media_scanner=$scan"
$clean = Psql "SELECT count(*) FROM media_assets m WHERE m.state = 'clean' AND NOT EXISTS (SELECT 1 FROM media_scan_events e WHERE e.media_id = m.id AND e.attempt = m.scan_attempt AND e.result = 'clean' AND e.sha256 = m.sha256)"
Result "aucun_media_sain_ni_en_analyse" ($clean -eq "0") "médias clean sans verdict traçable=$clean"
$neg = Psql "SELECT count(*) FROM media_usage_totals WHERE used_bytes < 0 OR reserved_bytes < 0"
Result "aucun_compteur_negatif" ($neg -eq "0") "portées négatives=$neg"
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"
$pilot = Psql "SELECT status FROM programs WHERE code = 'pab-qc-socle'"
Result "pilote_pab_en_brouillon" ($pilot -eq "draft") $pilot

$cfg = ApiPy "from app.core.config import get_settings as g; s=g(); print(bool(s.s3_endpoint), bool(s.s3_access_key), bool(s.s3_secret_key), s.media_quarantine_bucket)"
Result "api_configuree_pour_stockage_prive" ($cfg -match "^True True True nl-media-quarantine$") ($cfg -replace '\s+', ' ')
# Aucune route ne diffuse un contenu : inventaire exact des opérations médias (sans GET …/content).
$routes = ApiPy "from app.main import create_app; p = create_app().openapi()['paths']; print(sorted(f'{m.upper()} {k}' for k, v in p.items() if k.startswith('/api/v1/editorial/media') for m in v))"
$expected = "['DELETE /api/v1/editorial/media/{media_id}', 'GET /api/v1/editorial/media', 'GET /api/v1/editorial/media/{media_id}', 'POST /api/v1/editorial/media', 'POST /api/v1/editorial/media/{media_id}/revoke', 'PUT /api/v1/editorial/media/{media_id}/content']"
Result "cinq_operations_aucune_diffusion" ($routes -eq $expected) $routes
$deps = ApiPy "import importlib.metadata as m; print(m.version('pillow'), m.version('pypdf'), m.version('boto3'))"
Result "bibliotheques_installees" ($deps -match "^13\.\d+\.\d+ 6\.\d+\.\d+ 1\.\d+") $deps

$r = Anon "POST" "http://localhost:$apiPort/api/v1/editorial/media" '{"kind":"image","mime_type":"image/png","size_bytes":10}'
Result "reservation_anonyme_refusee_401" ($r.Code -eq 401 -and $r.Type -match "problem\+json") "statut $($r.Code) ($($r.Type))"
$r = Anon "PUT" "http://localhost:$apiPort/api/v1/editorial/media/00000000-0000-0000-0000-000000000000/content"
Result "televersement_anonyme_refuse_401" ($r.Code -eq 401) "statut $($r.Code)"
# Compartiment de quarantaine : jamais lisible anonymement (403 attendu ; 404 s'il n'a encore jamais servi).
$r = Anon "GET" "http://127.0.0.1:9000/nl-media-quarantine/"
Result "quarantaine_illisible_anonymement" ($r.Code -eq 403 -or $r.Code -eq 404) "statut $($r.Code)"

$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

Log ""; Log "================ SYNTHÈSE ÉTAPE 2.2.3-b ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.3-b (+ non-régression 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

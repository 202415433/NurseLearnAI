# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.2-c (catalogue public lu via l'édition publiée)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.2c-public-catalog) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2c.ps1
#
# 1. non-régression complète : scripts\validate-phase2.2.2b.ps1 (qui enchaîne 2.2.2-a, 2.2.1, 2.1,
#    2.0, 1.2 : qualité, audits, pile Docker reconstruite, pytest sur bases jetables — dont les tests
#    de catalogue —, Vitest, Playwright + axe) ;
# 2. contrôles propres à 2.2.2-c, en LECTURE SEULE sur l'API et la base de développement.
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\phase2.2.2c-<date>.log
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
$log = Join-Path $logDir "phase2.2.2c-$stamp.log"
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
$api = "http://localhost:$apiPort"
function Psql([string]$sql) { (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }

Log "NurseLearn AI — validation étape 2.2.2-c — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_2_2c_ou_ulterieure" ($branch -match '^phase-2\.2\.(2c|[3-9])' -or $branch -eq "main" -or $branch -like "security-*") $branch

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.2.2-b → 2.2.2-a → 2.2.1 → 2.1 → 2.0 → 1.2)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.2b.ps1 (enchaîne 2.2.2-a, 2.2.1, 2.1, 2.0 et 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.2b.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.2b-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_2b_et_anterieures" $false "aucun nouveau journal : validate-phase2.2.2b.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.2-b.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_2b_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    $pw = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] web_tests_vitest_playwright_axe" | Select-Object -Last 1
    Log "    pytest (dont test_catalog_editions.py et compatibilité manifest_v1) : $($py.Line.Trim())"
    Log "    Web (Vitest, Playwright, axe) : $($pw.Line.Trim())"
    Result "pytest_dont_catalogue_par_edition" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
    Result "parcours_web_playwright_axe" ("$($pw.Line)" -match "PASS") "journal $($reg12.Name)"
}

# -----------------------------------------------------------------------------
# 2. Contrôles 2.2.2-c (lecture seule)
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0006_ou_ulterieure" ($rev -match '^000[6-9]_') $rev
$total = Psql "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'"
Result "au_moins_71_tables" ([int]"0$total" -ge 71) "tables=$total"
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"

try {
    $pab = (Invoke-RestMethod -Uri "$api/api/v1/professions?jurisdiction=CA-QC" -ErrorAction Stop).items | Where-Object { $_.code -eq "pab" }
    $av = "$($pab.availability.published)|$($pab.availability.in_preparation)"
    Result "pilote_pab_non_expose" ($av -eq "0|1") "publié|en préparation = $av"
} catch { Result "pilote_pab_non_expose" $false "$($_.Exception.Message)" }

# Tout programme exposé est servi AVEC son édition publiée (aucun parcours incomplet).
try {
    $page = Invoke-RestMethod -Uri "$api/api/v1/programs?limit=100" -ErrorAction Stop
    $bad = @()
    foreach ($p in $page.items) {
        $d = Invoke-RestMethod -Uri "$api/api/v1/programs/$($p.id)" -ErrorAction Stop
        if (-not $d.release_no -or $d.modules.Count -lt 1 -or $d.modules.Count -ne $p.module_count) { $bad += $p.code }
    }
    Result "programmes_exposes_complets" ($bad.Count -eq 0) "exposés=$($page.total) incomplets=$($bad -join ',')"
} catch { Result "programmes_exposes_complets" $false "$($_.Exception.Message)" }

try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$api/api/v1/programs/00000000-0000-0000-0000-000000000000" -ErrorAction Stop
    Result "programme_inconnu_404" $false "statut $($r.StatusCode)"
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    Result "programme_inconnu_404" ($code -eq 404) "statut $code"
}

$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

Log ""; Log "================ SYNTHÈSE ÉTAPE 2.2.2-c ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-50} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.2-c (+ non-régression 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

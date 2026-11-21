# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.2-b (API éditoriale /api/v1/editorial)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.2b-editorial-api) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2b.ps1
#
# 1. non-régression complète : scripts\validate-phase2.2.2a.ps1 (qui enchaîne 2.2.1, 2.1, 2.0, 1.2 :
#    qualité, audits, pile Docker reconstruite, pytest sur bases jetables — dont les 30 tests de
#    l'API éditoriale —, Vitest, Playwright + axe) ;
# 2. contrôles propres à 2.2.2-b, en LECTURE SEULE : aucune écriture dans la base de développement,
#    aucun compte créé, aucune publication. Ne supprime aucun volume ni aucune donnée.
# Journal : docs\validation-logs\phase2.2.2b-<date>.log
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
$log = Join-Path $logDir "phase2.2.2b-$stamp.log"
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

Log "NurseLearn AI — validation étape 2.2.2-b — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_2_2b" ($branch -eq "phase-2.2.2b-editorial-api") $branch

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.2.2-a → 2.2.1 → 2.1 → 2.0 → 1.2)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.2a.ps1 (enchaîne 2.2.1, 2.1, 2.0 et 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.2a.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.2a-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_2a_et_anterieures" $false "aucun nouveau journal : validate-phase2.2.2a.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.2-a.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_2a_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    Log "    pytest (base, migrations, API dont test_editorial.py) : $($py.Line.Trim())"
    Result "pytest_dont_api_editoriale" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
}

# -----------------------------------------------------------------------------
# 2. Contrôles 2.2.2-b (lecture seule)
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "aucune_nouvelle_migration" ($rev -eq "0006_independent_versions") $rev
$total = Psql "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'"
Result "71_tables_inchangees" ($total -eq "71") "tables=$total"
$pilot = Psql "SELECT p.status || '|' || (SELECT count(*) FROM program_releases r WHERE r.program_id = p.id) FROM programs p WHERE p.code = 'pab-qc-socle'"
Result "pilote_pab_toujours_en_brouillon" ($pilot -eq "draft|0") "statut|éditions = $pilot"
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"

$ops = ApiPy "from app.main import create_app; p = create_app().openapi()['paths']; e = [(m, k) for k, v in p.items() if k.startswith('/api/v1/editorial') for m in v]; print(len(e), sorted(k for _, k in e if k.endswith('/publish')))"
Result "37_operations_editoriales_une_seule_publication" ($ops -match "^37 \['/api/v1/editorial/releases/\{release_id\}/publish'\]$") $ops
$flag = ApiPy "from app.core.config import get_settings; print(get_settings().medical_publication_enabled)"
Result "publication_medicale_desactivee" ($flag -eq "False") "medical_publication_enabled=$flag"

try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$apiPort/api/v1/editorial/programs" -ErrorAction Stop
    Result "acces_anonyme_refuse_401" $false "statut $($r.StatusCode)"
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    $ctype = "$($_.Exception.Response.ContentType)"
    Result "acces_anonyme_refuse_401" ($code -eq 401 -and $ctype -match "problem\+json") "statut $code ($ctype)"
}
try {
    $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://localhost:$apiPort/api/v1/editorial/programs" -ContentType "application/json" -Body "{}" -Headers @{ Cookie = "session=essai" } -ErrorAction Stop
    Result "cookie_sans_bearer_refuse_401" $false "statut $($r.StatusCode)"
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    Result "cookie_sans_bearer_refuse_401" ($code -eq 401) "statut $code"
}

$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

Log ""; Log "================ SYNTHÈSE ÉTAPE 2.2.2-b ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-50} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.2-b (+ non-régression 2.2.2-a, 2.2.1, 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

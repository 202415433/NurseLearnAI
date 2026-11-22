# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.3-a (migration 0007 : fondations des médias protégés)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.3-secure-media) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3a.ps1
#
# 1. non-régression complète : scripts\validate-phase2.2.2c.ps1 (enchaîne 2.2.2-b, 2.2.2-a, 2.2.1,
#    2.1, 2.0 et 1.2 : qualité, audits, pile Docker reconstruite — l'API applique 0007 au démarrage —,
#    pytest sur bases jetables dont test_media_foundation.py, Vitest, Playwright + axe) ;
# 2. contrôles propres à 2.2.3-a sur la base de développement, en LECTURE SEULE (les sondes
#    d'invariants sont des écritures REFUSÉES dans une transaction annulée : rien n'est modifié).
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\phase2.2.3a-<date>.log
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
$log = Join-Path $logDir "phase2.2.3a-$stamp.log"
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
function Psql([string]$sql) { (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }

Log "NurseLearn AI — validation étape 2.2.3-a — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
# Accepte aussi les sous-étapes suivantes (phase-2.2.3b-…), qui enchaînent ce script en non-régression.
Result "branche_phase_2_2_3" ($branch -like "phase-2.2.3*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
$hist = (git diff --name-only origin/main -- database/schema/0001_core.sql database/schema/0002_pgvector.sql database/schema/0003_business_rules.sql database/schema/0004_identity.sql database/schema/0005_learning_engine.sql database/schema/0006_independent_program_versions.sql 2>$null) -join " "
Result "migrations_historiques_inchangees" ([string]::IsNullOrWhiteSpace($hist)) $hist

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.2.2-c → … → 1.2)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.2c.ps1 (enchaîne 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0 et 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.2c.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.2c-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_2c_et_anterieures" $false "aucun nouveau journal : validate-phase2.2.2c.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.2-c.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_2c_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    Log "    pytest (dont test_media_foundation.py, migrations 0001→0007, API) : $($py.Line.Trim())"
    Result "pytest_dont_fondations_medias" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
}

# -----------------------------------------------------------------------------
# 2. Contrôles 2.2.3-a (base de développement)
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0007_en_tete" ($rev -eq "0007_secure_media_foundation") $rev
$total = Psql "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'"
Result "75_tables" ($total -eq "75") "tables=$total"
$tables = Psql "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('lesson_version_media','media_assets','media_scan_events','media_usage','learning_resources')"
Result "quatre_tables_medias_et_learning_resources_conservee" ($tables -eq "learning_resources,lesson_version_media,media_assets,media_scan_events,media_usage") $tables
$verify = (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -v ON_ERROR_STOP=1 -f /database/checks/verify_head.sql 2>&1 | ForEach-Object { "$_" }) -join " "
Result "schema_tete_verifie" ($verify -match "OK : 75 tables") $verify
$scan = Psql "SELECT count(*) FROM role_permissions WHERE permission_code = 'media:scan'"
Result "aucun_role_ne_detient_media_scan" ($scan -eq "0") "rôles=$scan"
$clean = Psql "SELECT count(*) FROM media_assets WHERE state = 'clean'"
Result "aucun_media_diffusable" ($clean -eq "0") "médias clean=$clean"
$probe = Psql "BEGIN; INSERT INTO role_permissions (role_id, permission_code) SELECT id, 'media:scan' FROM roles WHERE code = 'admin'; ROLLBACK;"
Result "invariant_media_scan_refuse_a_l_admin" ($probe -match "media_scanner") "sonde annulée"
$probe = Psql "BEGIN; INSERT INTO media_usage (scope_kind, quota_bytes) VALUES ('platform', 1); ROLLBACK;"
Result "invariant_quota_non_falsifiable" ($probe -match "quota par défaut") "sonde annulée"
$cols = Psql "SELECT count(*) FROM information_schema.columns WHERE table_name = 'media_usage' AND column_name IN ('used_bytes', 'reserved_bytes')"
Result "occupation_derivee_sans_compteur_stocke" ($cols -eq "0") "colonnes de compteur=$cols"
$probe = Psql "BEGIN; SELECT nl_media_default_quota('user'), nl_media_default_quota('organization'), nl_media_default_quota('platform'); ROLLBACK;"
Result "quotas_1_5_20_gio" ($probe -match "1073741824\|5368709120\|21474836480") $probe
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"
$pilot = Psql "SELECT status FROM programs WHERE code = 'pab-qc-socle'"
Result "pilote_pab_en_brouillon" ($pilot -eq "draft") $pilot

$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

Log ""; Log "================ SYNTHÈSE ÉTAPE 2.2.3-a ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.3-a (+ non-régression 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

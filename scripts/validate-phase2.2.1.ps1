# =============================================================================
# NurseLearn AI — Validation du sous-lot 2.2.1 (modèle de données et règles éditoriales)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.1-learning-data-model) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.1.ps1
#
# 1. non-régression complète : scripts\validate-phase2.1.ps1 (qualité, audits, pile Docker,
#    validate-phase1.2.ps1 avec pytest — dont les tests PostgreSQL de la migration 0005 —,
#    Vitest, Playwright + axe, parcours d'identité) ;
# 2. contrôles propres au sous-lot 2.2.1 sur la base de développement migrée en 0005.
# Ne supprime aucun volume ni aucune donnée : chaque sonde d'écriture interdite s'exécute dans
# une transaction annulée (ROLLBACK). Journal : docs\validation-logs\phase2.2.1-<date>.log
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
$log = Join-Path $logDir "phase2.2.1-$stamp.log"
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

Log "NurseLearn AI — validation sous-lot 2.2.1 — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_2_1" ($branch -eq "phase-2.2.1-learning-data-model") $branch

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.1 → 2.0 → 1.2)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.1.ps1 (inclut validate-phase1.2.ps1)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.1.ps1" | Out-Null
$reg21 = Get-ChildItem "$logDir\phase2.1-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg21) {
    Result "non_regression_phase_2_1" $false "aucun nouveau journal : validate-phase2.1.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg21.FullName -Pattern "^Total : (\d+) contrôles Phase 2\.1.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg21.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_phase_2_1" ($fails -eq 0) "$($summary.Line) — journal $($reg21.Name)"
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    $pw = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] web_tests_vitest_playwright_axe" | Select-Object -Last 1
    Log "    pytest (base dont 0005, API, migrations) : $($py.Line.Trim())"
    Log "    Web : $($pw.Line.Trim())"
    Result "pytest_dont_tests_0005" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
}

# -----------------------------------------------------------------------------
# 2. Contrôles du sous-lot 2.2.1 (base de développement)
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0005_en_tete" ($rev -eq "0005_learning_engine") $rev
$tables = Psql "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_name IN ('program_releases','enrollments','learning_events','editorial_decisions')"
Result "quatre_nouvelles_tables" ($tables -eq "editorial_decisions,enrollments,learning_events,program_releases") $tables
$check = Psql "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_lesson_versions_status'"
Result "etats_editoriaux_medical_review" ($check -match "medical_review" -and $check -notmatch "in_review") $check
$legacy = Psql "SELECT count(*) FROM lesson_versions WHERE status = 'in_review'"
Result "aucun_ancien_code_in_review" ($legacy -eq "0") "lignes=$legacy"

$pilot = Psql "SELECT p.status || '|' || (SELECT count(*) FROM program_releases r WHERE r.program_id = p.id) || '|' || (SELECT count(*) FROM lesson_versions lv JOIN lessons l ON l.id = lv.lesson_id JOIN modules m ON m.id = l.module_id WHERE m.code = 'hygiene-mains-prevention-infections' AND lv.status <> 'draft') FROM programs p WHERE p.code = 'pab-qc-socle'"
Result "pilote_pab_toujours_en_brouillon" ($pilot -eq "draft|0|0") "statut|éditions|versions non brouillon = $pilot"
$pub = Psql "SELECT (SELECT count(*) FROM programs WHERE status = 'published') || '|' || (SELECT count(*) FROM program_releases WHERE status = 'published')"
Result "aucun_contenu_publie" ($pub -eq "0|0") "programmes|éditions publiés = $pub"

# Sondes d'écriture interdite : toutes dans une transaction annulée.
$p1 = Psql "BEGIN; UPDATE programs SET status = 'published' WHERE code = 'pab-qc-socle'; ROLLBACK;"
Result "publication_directe_programme_refusee" ($p1 -match "ERROR|ERREUR") $p1
$p2 = Psql "BEGIN; UPDATE lesson_versions SET status = 'medical_review' WHERE id = (SELECT lv.id FROM lesson_versions lv JOIN lessons l ON l.id = lv.lesson_id JOIN modules m ON m.id = l.module_id WHERE m.code = 'hygiene-mains-prevention-infections' LIMIT 1); ROLLBACK;"
Result "transition_sans_acteur_refusee" ($p2 -match "ERROR|ERREUR") $p2
$p3 = Psql "BEGIN; INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body, status) SELECT l.id, 'fr', 99, 'Sonde', jsonb_build_object('blocks', jsonb_build_array()), 'published' FROM lessons l LIMIT 1; ROLLBACK;"
Result "insertion_directe_publiee_refusee" ($p3 -match "ERROR|ERREUR") $p3
$p4 = Psql "BEGIN; TRUNCATE editorial_decisions; ROLLBACK;"
Result "journal_editorial_ajout_seul" ($p4 -match "ERROR|ERREUR") $p4
$p5 = Psql "BEGIN; TRUNCATE learning_events; ROLLBACK;"
Result "historique_apprentissage_ajout_seul" ($p5 -match "ERROR|ERREUR") $p5
$p6 = Psql "BEGIN; SELECT set_config('nl.editorial_log', 'off', true); INSERT INTO editorial_decisions (entity_type, entity_id, from_status, to_status, decision, actor_id) SELECT 'lesson_version', gen_random_uuid(), 'medical_review', 'approved', 'approved', id FROM users LIMIT 1; ROLLBACK;"
Result "decision_forgee_refusee" ($p6 -match "ERROR|ERREUR") $p6

$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

Log ""; Log "================ SYNTHÈSE SOUS-LOT 2.2.1 ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-44} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.1 (+ non-régression 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

# =============================================================================
# NurseLearn AI — Validation de l'étape 2.2.2-a (migration 0006, versions de modules, D12)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.2.2a-versioning-migration) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.2a.ps1
#
# 1. non-régression complète : scripts\validate-phase2.2.1.ps1 (qui enchaîne 2.1, 2.0 et 1.2 :
#    qualité, audits, pile Docker, pytest — dont les tests PostgreSQL de 0006 —, Vitest, Playwright) ;
# 2. contrôles propres à l'étape 2.2.2-a sur la base de développement migrée en 0006.
# Ne supprime aucun volume ni aucune donnée ; ne déclenche AUCUN retour arrière sur la base de
# développement (le retour arrière est éprouvé par pytest sur des bases jetables). Chaque sonde
# d'écriture interdite s'exécute dans une transaction annulée. Journal : docs\validation-logs\phase2.2.2a-<date>.log
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
$log = Join-Path $logDir "phase2.2.2a-$stamp.log"
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

Log "NurseLearn AI — validation étape 2.2.2-a — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_2_2a_ou_ulterieure" ($branch -match '^phase-2\.2\.2' -or $branch -eq "main") $branch

# -----------------------------------------------------------------------------
# 1. Non-régression complète (2.2.1 → 2.1 → 2.0 → 1.2)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.1.ps1 (enchaîne 2.1, 2.0 et 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.1.ps1" | Out-Null
$reg221 = Get-ChildItem "$logDir\phase2.2.1-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg221) {
    Result "non_regression_2_2_1_et_anterieures" $false "aucun nouveau journal : validate-phase2.2.1.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg221.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.1.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg221.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_1_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg221.Name)"
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    $pw = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] web_tests_vitest_playwright_axe" | Select-Object -Last 1
    Log "    pytest (base dont 0005 historique et 0006, migrations, API) : $($py.Line.Trim())"
    Log "    Web : $($pw.Line.Trim())"
    Result "pytest_dont_tests_0006" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
}

# -----------------------------------------------------------------------------
# 2. Contrôles de l'étape 2.2.2-a (base de développement)
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0006_en_tete" ($rev -eq "0006_independent_versions") $rev
$tables = Psql "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_name IN ('module_versions','module_version_lessons','module_version_lesson_versions','program_release_modules','program_release_prerequisites')"
Result "cinq_nouvelles_tables" ($tables -eq "module_version_lesson_versions,module_version_lessons,module_versions,program_release_modules,program_release_prerequisites") $tables
# Même périmètre que verify_head.sql : tables du modèle, hors table technique alembic_version.
$total = Psql "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'"
Result "71_tables" ($total -eq "71") "tables=$total"
$idx = Psql "SELECT count(*) FROM pg_indexes WHERE indexname = 'ux_lesson_versions_one_published'"
Result "index_unicite_publication_leve" ($idx -eq "0") "index=$idx"
$legacy = Psql "SELECT count(*) FILTER (WHERE structure = 'manifest_v1') || '|' || count(*) FILTER (WHERE structure = 'module_versions') FROM program_releases"
Result "editions_existantes_conservees" ($legacy -match '^\d+\|\d+$') "manifest_v1|module_versions = $legacy"
$pilot = Psql "SELECT p.status || '|' || (SELECT count(*) FROM program_releases r WHERE r.program_id = p.id) || '|' || (SELECT count(*) FROM module_versions mv JOIN modules m ON m.id = mv.module_id WHERE m.code = 'hygiene-mains-prevention-infections') FROM programs p WHERE p.code = 'pab-qc-socle'"
Result "pilote_pab_toujours_en_brouillon" ($pilot -eq "draft|0|0") "statut|éditions|versions de module = $pilot"
$pub = Psql "SELECT (SELECT count(*) FROM programs WHERE status = 'published') || '|' || (SELECT count(*) FROM program_releases WHERE status = 'published') || '|' || (SELECT count(*) FROM module_versions WHERE status = 'published')"
Result "aucun_contenu_publie" ($pub -eq "0|0|0") "programmes|éditions|versions de module publiés = $pub"
$verify = (Get-Content "$root\database\checks\verify_head.sql" -Raw | docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -v ON_ERROR_STOP=1 2>&1 | ForEach-Object { "$_" }) -join " "
Result "schema_tete_verifie" ($LASTEXITCODE -eq 0) $verify

# Sondes d'écriture interdite : toutes dans une transaction annulée.
$p1 = Psql "BEGIN; INSERT INTO program_releases (program_id, release_no, author_id, structure) SELECT p.id, 1, (SELECT id FROM users LIMIT 1), 'manifest_v1' FROM programs p WHERE p.code = 'pab-qc-socle'; ROLLBACK;"
Result "nouvelle_edition_manifeste_refusee" ($p1 -match "structure.*obligatoire") $p1
$p2 = Psql "BEGIN; INSERT INTO module_versions (module_id, version_no, author_id, status) SELECT m.id, 1, (SELECT id FROM users LIMIT 1), 'approved' FROM modules m WHERE m.code = 'hygiene-mains-prevention-infections'; ROLLBACK;"
Result "version_de_module_approuvee_d_office_refusee" ($p2 -match "brouillon") $p2
$p3 = Psql "BEGIN; INSERT INTO medical_references (kind, title, url, last_verified_at) VALUES ('guideline', 'Sonde', 'https://example.test/sonde', current_date); ROLLBACK;"
Result "verification_de_source_sans_relecteur_refusee" ($p3 -match "nl.actor_id|relecteur") $p3
$p4 = Psql "BEGIN; INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position) SELECT gen_random_uuid(), l.module_id, l.id, 0 FROM lessons l LIMIT 1; ROLLBACK;"
Result "lien_incoherent_refuse_par_cle_etrangere" ($p4 -match "foreign key|clé étrangère") $p4

$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

Log ""; Log "================ SYNTHÈSE ÉTAPE 2.2.2-a ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-46} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles 2.2.2-a (+ non-régression 2.2.1, 2.1, 2.0, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

# =============================================================================
# NurseLearn AI — Validation du correctif D14 (séparation des rôles PostgreSQL, migration 0009)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche security-d14-postgres-role-separation) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-d14-postgres-roles.ps1
#
# 0. état Git ; secrets de connexion présents dans .env (générés s'ils manquent, jamais affichés) ;
# 1. INVENTAIRE et SAUVEGARDE de la base existante AVANT toute modification (pg_dump -Fc dans
#    backups\, hors Git) ; procédure de reprise : voir docs\VALIDATION-D14-POSTGRES-ROLES.md § 6 ;
# 2. non-régression complète : scripts\validate-phase2.2.3c.ps1 (pile reconstruite : le conteneur
#    `migrate` applique 0009 ; l'API se connecte en nl_api ; pytest base + API sous nl_api ; ClamAV
#    et EICAR réels ; 2.2.3-b … 1.2) ;
# 3. contrôles D14 : rôles, propriétaires, privilèges, fonctions SECURITY DEFINER, connexions
#    réellement utilisées, sonde adversariale DANS le conteneur de l'API, intégrité des données.
# Ne supprime aucun volume ni aucune donnée. Journal : docs\validation-logs\d14-<date>.log
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
$log = Join-Path $logDir "d14-$stamp.log"
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
function NewSecret { $b = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); return (-join ($b | ForEach-Object { $_.ToString("x2") })) }

Log "NurseLearn AI — validation D14 (rôles PostgreSQL) — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_d14" ($branch -like "security-d14*" -or $branch -like "security-r3*") $branch
$dirty = (git status --porcelain --untracked-files=no 2>$null | Where-Object { $_ -notmatch 'docs/validation-logs' }) -join " "
Result "git_sans_modification_locale" ([string]::IsNullOrWhiteSpace($dirty)) $dirty
$hist = (git diff --diff-filter=MD --name-only origin/main...HEAD -- database/schema 2>$null) -join " "
Result "migrations_historiques_inchangees" ([string]::IsNullOrWhiteSpace($hist)) $hist
Result "migration_0009_presente" ((Test-Path "$root\database\schema\0009_postgres_role_separation.sql") -and (Test-Path "$root\services\api\alembic\versions\0009_postgres_role_separation.py")) ""

# -----------------------------------------------------------------------------
# 0. Secrets de connexion (fichier .env local, jamais versionné)
# -----------------------------------------------------------------------------
$envVars = ReadEnv
foreach ($k in 'API_DB_PASSWORD', 'MEDIA_SCANNER_DB_PASSWORD') {
    if (-not $envVars[$k]) {
        if ((Get-Content "$root\.env") -match "^\s*$k=") {
            (Get-Content "$root\.env") -replace "^\s*$k=.*", "$k=$(NewSecret)" | Set-Content "$root\.env" -Encoding ASCII
        } else {
            Add-Content -Path "$root\.env" -Value "`r`n$k=$(NewSecret)" -Encoding ASCII
        }
        Log "    $k absent de .env : valeur aléatoire ajoutée (non affichée)."
        $envVars = ReadEnv
    }
}
# R3 : identités S3 par service (32 caractères hexadécimaux : limite S3 de 40), requises par Compose.
foreach ($k in 'S3_API_SECRET_KEY', 'S3_SCANNER_SECRET_KEY', 'S3_READER_SECRET_KEY') {
    if (-not $envVars[$k]) {
        $v = (NewSecret).Substring(0, 32)
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
Result "secrets_de_connexion_presents" ($envVars['API_DB_PASSWORD'].Length -ge 32 -and $envVars['MEDIA_SCANNER_DB_PASSWORD'].Length -ge 32) ""
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
function Psql([string]$sql, [string]$db = $PGDB) { (docker compose exec -T postgres psql -U $PGU -d $db -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }
$COUNTS = "SELECT (SELECT count(*) FROM users) || ' ' || (SELECT count(*) FROM user_roles) || ' ' || (SELECT count(*) FROM organizations) || ' ' || (SELECT count(*) FROM programs) || ' ' || (SELECT count(*) FROM lesson_versions) || ' ' || (SELECT count(*) FROM program_releases) || ' ' || (SELECT count(*) FROM media_assets) || ' ' || (SELECT count(*) FROM media_scan_events) || ' ' || (SELECT count(*) FROM editorial_decisions) || ' ' || (SELECT count(*) FROM audit_logs)"

# -----------------------------------------------------------------------------
# 1. Inventaire et sauvegarde AVANT modification
# -----------------------------------------------------------------------------
Log ""; Log ">>> Inventaire et sauvegarde de la base existante (avant 0009)"
docker compose up -d --wait postgres 2>&1 | Out-Null
$revBefore = Psql "SELECT version_num FROM alembic_version"
$rolesBefore = Psql "SELECT string_agg(rolname || CASE WHEN rolsuper THEN '(super)' ELSE '' END, ',' ORDER BY rolname) FROM pg_roles WHERE rolname !~ '^pg_'"
$ownersBefore = Psql "SELECT string_agg(DISTINCT pg_get_userbyid(relowner)::text, ',') FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
$countsBefore = Psql $COUNTS
Log "    migration=$revBefore ; rôles=$rolesBefore ; propriétaires=$ownersBefore"
Log "    volumes (users user_roles organizations programs lesson_versions program_releases media_assets media_scan_events editorial_decisions audit_logs) = $countsBefore"
$bdir = Join-Path $root "backups"; New-Item -ItemType Directory -Force $bdir | Out-Null
$dump = Join-Path $bdir "$PGDB-avant-d14-$stamp.dump"
docker compose exec -T postgres sh -c "pg_dump -U $PGU -d $PGDB -Fc -f /tmp/avant-d14.dump" 2>&1 | Out-Null
docker compose cp "postgres:/tmp/avant-d14.dump" $dump 2>&1 | Out-Null
$entries = (docker compose exec -T postgres sh -c "pg_restore -l /tmp/avant-d14.dump | grep -c ' TABLE DATA '" 2>&1 | Out-String).Trim()
$size = if (Test-Path $dump) { (Get-Item $dump).Length } else { 0 }
Result "sauvegarde_avant_migration" ($size -gt 10000 -and [int]("0$entries" -replace '\D', '') -ge 70) ("{0:N0} octets, {1} tables de données — {2}" -f $size, $entries, (Split-Path $dump -Leaf))

# -----------------------------------------------------------------------------
# 2. Non-régression complète (2.2.3-c → … → 1.2) : applique 0009 et exécute l'API en nl_api
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase2.2.3c.ps1 (ClamAV, EICAR, 2.2.3-b … 1.2)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase2.2.3c.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase2.2.3c-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
$reg12 = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_2_2_3c_et_anterieures" $false "aucun nouveau journal"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles 2\.2\.3-c.*, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_2_2_3c_et_anterieures" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
    $worker = Select-String -Path $reg.FullName -Pattern "tests_worker_clamav_reel|eicar_detecte" | ForEach-Object { $_.Line.Trim() }
    foreach ($l in $worker) { Log "    $l" }
}
if ($reg12) {
    $py = Select-String -Path $reg12.FullName -Pattern "=> \[(PASS|FAIL)\] pytest_complet" | Select-Object -Last 1
    Log "    pytest base + API (application connectée en nl_api) : $($py.Line.Trim())"
    Result "pytest_complet_api_en_nl_api" ("$($py.Line)" -match "PASS") "journal $($reg12.Name)"
}

# -----------------------------------------------------------------------------
# 3. Contrôles D14
# -----------------------------------------------------------------------------
$rev = Psql "SELECT version_num FROM alembic_version"
Result "migration_0009_appliquee" ($rev -eq "0009_postgres_role_separation") $rev
$migrate = (docker compose ps -a migrate --format "{{.ExitCode}}" 2>$null | Select-Object -First 1)
Result "migrations_par_le_conteneur_migrate" ("$migrate".Trim() -eq "0") "code de sortie=$migrate"
$verify = (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -v ON_ERROR_STOP=1 -f /database/checks/verify_head.sql 2>&1 | Out-String)
Result "schema_tete_verifie_avec_d14" ($verify -match "OK : 75 tables") (($verify -split "`n" | Select-String "OK :|ERR|ERREUR|EXCEPTION") -join " ")

$roles = Psql "SELECT string_agg(rolname || ':' || rolsuper || '/' || rolcreatedb || '/' || rolcreaterole || '/' || rolbypassrls || '/' || rolcanlogin, ' ' ORDER BY rolname) FROM pg_roles WHERE rolname IN ('nl_owner', 'nl_api', 'nl_media_scanner')"
Result "roles_sans_privilege" ($roles -eq "nl_api:false/false/false/false/true nl_media_scanner:false/false/false/false/true nl_owner:false/false/false/false/false") $roles
$members = Psql "SELECT count(*) FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member WHERE g.rolname = 'nl_owner' OR u.rolname IN ('nl_api', 'nl_media_scanner', 'nl_owner')"
Result "aucune_appartenance_de_role" ($members -eq "0") "appartenances=$members"
$owners = Psql "SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relowner <> 'nl_owner'::regrole AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')) || ' ' || (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proowner <> 'nl_owner'::regrole AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')) || ' ' || (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public')"
Result "tout_le_schema_appartient_a_nl_owner" ($owners -eq "0 0 nl_owner") "relations/fonctions hors nl_owner, propriétaire du schéma = $owners"
$secdef = Psql "SELECT string_agg(p.proname || '=' || has_function_privilege('public', p.oid, 'EXECUTE') || '/' || (p.proconfig IS NOT NULL), ' ' ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.prosecdef"
Result "security_definer_verrouillees" ($secdef -eq "nl_media_reserve=false/true nl_media_scanner_audit=false/true nl_media_scanner_identity=false/true nl_media_scanner_provision=false/true nl_media_usage_row=false/true") $secdef
$privs = Psql "SELECT (SELECT count(*) FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v') AND (has_table_privilege('nl_api', c.oid, 'TRUNCATE') OR has_table_privilege('nl_api', c.oid, 'TRIGGER') OR has_table_privilege('nl_api', c.oid, 'REFERENCES'))) || ' ' || has_schema_privilege('nl_api', 'public', 'CREATE') || ' ' || has_database_privilege('nl_api', current_database(), 'TEMPORARY') || ' ' || has_table_privilege('nl_api', 'media_scan_events', 'INSERT') || ' ' || has_table_privilege('nl_api', 'media_usage', 'UPDATE') || ' ' || has_table_privilege('nl_api', 'role_permissions', 'INSERT') || ' ' || has_table_privilege('nl_api', 'audit_logs', 'DELETE') || ' ' || has_column_privilege('nl_api', 'media_assets', 'clean_key', 'UPDATE')"
Result "privileges_api_minimaux" ($privs -eq "0 false false false false false false false") "TRUNCATE/TRIGGER/REFERENCES, CREATE, TEMP, verdicts, quotas, permissions, audit, clean_key = $privs"

# Connexions réellement utilisées : l'API en nl_api, sans identifiant d'administration.
$apiUrl = (docker compose exec -T api printenv DATABASE_URL 2>$null | Out-String).Trim()
$apiAdmin = (docker compose exec -T api sh -c 'printenv POSTGRES_PASSWORD API_DB_PASSWORD MEDIA_SCANNER_DB_PASSWORD | wc -l' 2>$null | Out-String).Trim()
Result "api_connectee_en_nl_api_sans_secret_admin" ($apiUrl -match '^postgresql\+asyncpg://nl_api:' -and $apiAdmin -eq "0") "variables d'administration dans l'API = $apiAdmin"
$sessions = Psql "SELECT string_agg(DISTINCT usename, ',' ORDER BY usename) FROM pg_stat_activity WHERE datname = current_database() AND usename IS NOT NULL AND pid <> pg_backend_pid()"
Result "sessions_applicatives_non_privilegiees" ($sessions -match "nl_api" -and ",$sessions," -notmatch ",$PGU,") "sessions=$sessions (administration $PGU absente hors de cette sonde)"

# Sonde adversariale DANS le conteneur de l'API (sa propre connexion), transactions annulées.
docker compose cp "$root\scripts\d14_api_probe.py" "api:/tmp/d14_api_probe.py" 2>&1 | Out-Null
$probe = (docker compose exec -T api python /tmp/d14_api_probe.py 2>&1 | Out-String).Trim()
Log "    sonde API : $probe"
$pj = $null; try { $pj = ($probe -split "`n" | Select-Object -Last 1) | ConvertFrom-Json } catch { }
Result "sonde_adversariale_api_tout_refuse" ($pj -and $pj.user -eq "nl_api" -and $pj.refused -eq $pj.total -and $pj.total -ge 20) $(if ($pj) { "$($pj.refused)/$($pj.total) refusées, acceptées : $($pj.accepted -join ',')" } else { "sonde illisible" })

# Intégrité : aucune donnée perdue par la migration (les compteurs ne diminuent jamais).
$countsAfter = Psql $COUNTS
$b = "$countsBefore" -split ' '; $a = "$countsAfter" -split ' '
$lost = 0; for ($i = 0; $i -lt $b.Count; $i++) { if ([int64]$a[$i] -lt [int64]$b[$i]) { $lost++ } }
Result "integrite_aucune_donnee_perdue" ($a.Count -eq 10 -and $lost -eq 0) "avant=[$countsBefore] après=[$countsAfter]"
$med = Psql "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'"
Result "aucun_programme_medical_publie" ($med -eq "0") "programmes médicaux publiés = $med"
$pilot = Psql "SELECT status FROM programs WHERE code = 'pab-qc-socle'"
Result "pilote_pab_en_brouillon" ($pilot -eq "draft") $pilot
$routes = (docker compose exec -T api python -c "from app.main import create_app; p = create_app().openapi()['paths']; print(sum(1 for k in p if k.startswith('/api/v1/editorial/media')))" 2>&1 | Out-String).Trim()
Result "aucune_route_de_diffusion_ajoutee" ($routes -eq "3") "chemins médias=$routes"

Log ""; Log "================ SYNTHÈSE D14 ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-55} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles D14 (+ non-régression 2.2.3-c, 2.2.3-b, 2.2.3-a, 2.2.2-c, 2.2.2-b, 2.2.2-a, 2.2.1, 2.1, 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Sauvegarde avant migration : $dump (hors Git ; reprise : docs\VALIDATION-D14-POSTGRES-ROLES.md § 6)"
Log "Journal : $log"
if ($fail -gt 0) { exit 1 } else { exit 0 }

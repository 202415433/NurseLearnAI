# =============================================================================
# NurseLearn AI — Validation de la Phase 2.1 (identité, sessions, rôles, invitations)
# Usage (PowerShell, à la racine du dépôt C:\NurseLearnAI-git, branche phase-2.1-identity-access) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.1.ps1
#
# 1. met à jour package-lock.json (sans rien installer dans votre dossier) ;
# 2. audits de dépendances (npm audit, pip-audit — dont argon2-cffi) ;
# 3. qualité : Ruff, mypy, ESLint, Prettier, TypeScript ;
# 4. non-régression complète : scripts\validate-phase1.2.ps1 (pile Docker, pytest dont les tests
#    d'identité et Mailpit, Vitest, Playwright + axe dont le parcours d'identité FR/EN) ;
# 5. contrôles propres à la Phase 2.1 sur la pile démarrée : migration 0004, garde de rôles en base,
#    parcours API complet avec courriel réel dans Mailpit, sessions, journal d'audit sans secret.
# Ne supprime aucun volume ni aucune donnée. Crée seulement un compte étudiant de validation
# (adresse en @example.test) et un établissement « validation-phase-2-1 ».
# Journal : docs\validation-logs\phase2.1-<date>.log
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
$log = Join-Path $logDir "phase2.1-$stamp.log"
$results = [ordered]@{}

function Log([string]$msg) { Write-Host $msg; Add-Content -Path $script:log -Value $msg -Encoding UTF8 }
function Run([string]$title, [scriptblock]$cmd) {
    Log ""; Log ">>> $title"
    $out = & $cmd 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    foreach ($l in $out) { Log "    $l" }
    Log "    [code retour : $code]"
    return @{ Code = $code; Out = ($out -join "`n") }
}
function Result([string]$name, [bool]$ok, [string]$detail = "") {
    $script:results[$name] = $ok
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    Log "    => [$tag] $name $detail"
}
# Appel JSON de l'API : renvoie le statut HTTP et le corps décodé (jamais journalisé tel quel).
function CallApi([string]$method, [string]$path, $body = $null, [string]$token = "", [hashtable]$extra = @{}) {
    $headers = @{ Accept = "application/json" } + $extra
    if ($token) { $headers["Authorization"] = "Bearer $token" }
    $params = @{ UseBasicParsing = $true; TimeoutSec = 20; Uri = "$script:api$path"; Method = $method; Headers = $headers }
    if ($null -ne $body) { $params["Body"] = ($body | ConvertTo-Json -Compress); $params["ContentType"] = "application/json" }
    try {
        $r = Invoke-WebRequest @params
        $data = $null; if ($r.Content) { try { $data = $r.Content | ConvertFrom-Json } catch { } }
        return @{ Status = [int]$r.StatusCode; Data = $data }
    } catch {
        $st = 0; $data = $null
        if ($_.Exception.Response) {
            $st = [int]$_.Exception.Response.StatusCode
            try { $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream()); $data = $sr.ReadToEnd() | ConvertFrom-Json } catch { }
        }
        return @{ Status = $st; Data = $data }
    }
}
function MailToken([string]$to, [string]$path) {
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $q = [uri]::EscapeDataString("to:`"$to`"")
            $found = Invoke-RestMethod -TimeoutSec 10 -Uri "$script:mailpit/api/v1/search?query=$q"
            foreach ($m in $found.messages) {
                $detail = Invoke-RestMethod -TimeoutSec 10 -Uri "$script:mailpit/api/v1/message/$($m.ID)"
                $match = [regex]::Match("$($detail.Text)", [regex]::Escape($path) + '([A-Za-z0-9_-]{20,128})')
                if ($match.Success) { return $match.Groups[1].Value }
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

$envVars = @{}
Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envVars[$k.Trim()] = $v.Trim() }
$PGU = $envVars['POSTGRES_USER']; if (-not $PGU) { $PGU = "nurselearn" }
$PGDB = $envVars['POSTGRES_DB']; if (-not $PGDB) { $PGDB = "nurselearn" }
$apiPort = if ($envVars['API_PORT']) { $envVars['API_PORT'] } else { "28000" }
$webPort = if ($envVars['WEB_PORT']) { $envVars['WEB_PORT'] } else { "3000" }
$api = "http://localhost:$apiPort"; $web = "http://localhost:$webPort"; $mailpit = "http://localhost:8025"
function Psql([string]$sql) { docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" } }

Log "NurseLearn AI — validation Phase 2.1 — $(Get-Date -Format o)"
$branch = git branch --show-current 2>$null
Log "Git : $branch @ $(git rev-parse --short HEAD 2>$null)"
Result "branche_phase_2_1" ($branch -eq "phase-2.1-identity-access") $branch

# -----------------------------------------------------------------------------
# 1. package-lock.json aligné sur package.json
# -----------------------------------------------------------------------------
$r = Run "Mise à jour de package-lock.json (scripts/npm-lock.sh)" {
    docker run --rm -v "${root}:/repo" node:22-bookworm sh /repo/scripts/npm-lock.sh
}
Result "lock_npm_coherent" ($r.Code -eq 0 -and $r.Out -match 'lock-ok')
if (-not $results["lock_npm_coherent"]) {
    Log ""; Log "ARRÊT : package-lock.json n'a pas pu être mis à jour. Le lockfile du dépôt est intact."
    Log "Journal : $log"
    exit 1
}

# -----------------------------------------------------------------------------
# 2. Audits de dépendances
# -----------------------------------------------------------------------------
$r = Run "npm audit" {
    docker run --rm -v "${root}:/repo" -w /repo/apps/web node:22-bookworm sh -c "npm audit --package-lock-only --json > /repo/docs/validation-logs/npm-audit-$stamp.json; npm audit --package-lock-only --audit-level=high; echo audit-code=`$?"
}
$audit = $null
try { $audit = Get-Content "$logDir\npm-audit-$stamp.json" -Raw | ConvertFrom-Json } catch { }
$v = if ($audit) { $audit.metadata.vulnerabilities } else { $null }
$detail = if ($v) { "critiques=$($v.critical) élevées=$($v.high) modérées=$($v.moderate) faibles=$($v.low)" } else { "rapport illisible" }
Result "npm_aucune_vulnerabilite_elevee_ou_critique" ($v -and $v.critical -eq 0 -and $v.high -eq 0) $detail
$r = Run "pip-audit (dépendances de l'API, dont argon2-cffi)" {
    docker run --rm -v "${root}:/repo" -w /repo/services/api -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c "pip install -q --root-user-action=ignore -e '.[dev]' pip-audit && pip freeze --exclude-editable > /tmp/req.txt && pip-audit --strict --disable-pip --no-deps -r /tmp/req.txt"
}
Result "pip_audit_sans_vulnerabilite" ($r.Code -eq 0)

# -----------------------------------------------------------------------------
# 3. Qualité du code
# -----------------------------------------------------------------------------
$r = Run "Python : Ruff (lint + format) et mypy" {
    docker run --rm -v "${root}:/repo" -w /repo/services/api -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c "pip install -q --root-user-action=ignore -e '.[dev]' && cd /repo && ruff check . && ruff format --check . && cd services/api && mypy"
}
Result "python_ruff_mypy" ($r.Code -eq 0)
$r = Run "Web : ESLint, Prettier, TypeScript" {
    docker run --rm -v "${root}:/repo" -v /repo/apps/web/node_modules -w /repo/apps/web node:22-bookworm sh -c "npm ci --no-audit --no-fund --loglevel=error && npm run lint && npm run format:check && npm run typecheck"
}
Result "web_eslint_prettier_typescript" ($r.Code -eq 0)

# -----------------------------------------------------------------------------
# 4. Non-régression complète (pile Docker, pytest, Vitest, Playwright + axe)
# -----------------------------------------------------------------------------
Log ""; Log ">>> Non-régression : scripts\validate-phase1.2.ps1 (inclut désormais les tests d'identité et Mailpit)"
$regStart = Get-Date
powershell -ExecutionPolicy Bypass -File "$root\scripts\validate-phase1.2.ps1" | Out-Null
$reg = Get-ChildItem "$logDir\phase1.2-*.log" | Where-Object { $_.CreationTime -ge $regStart } | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $reg) {
    Result "non_regression_phase_1_2" $false "aucun nouveau journal : validate-phase1.2.ps1 n'a pas démarré"
} else {
    $summary = Select-String -Path $reg.FullName -Pattern "^Total : (\d+) contrôles, (\d+) échec" | Select-Object -Last 1
    $fails = if ($summary) { [int]$summary.Matches[0].Groups[2].Value } else { -1 }
    Select-String -Path $reg.FullName -Pattern "=> \[FAIL\]" | ForEach-Object { Log "    $($_.Line.Trim())" }
    Result "non_regression_phase_1_2" ($fails -eq 0) "$($summary.Line) — journal $($reg.Name)"
    $py = Select-String -Path $reg.FullName -Pattern "pytest_complet" | Select-Object -Last 1
    $pw = Select-String -Path $reg.FullName -Pattern "web_tests_vitest_playwright_axe" | Select-Object -Last 1
    Log "    pytest : $($py.Line.Trim())"; Log "    web : $($pw.Line.Trim())"
    # Les tests d'identité ne doivent pas avoir été ignorés (Mailpit joignable depuis les tests).
    $content = Get-Content $reg.FullName -Raw
    Result "tests_mailpit_executes" ($content -notmatch "MAILPIT_URL non d") "aucun test Mailpit ignoré"
}

# -----------------------------------------------------------------------------
# 5. Contrôles Phase 2.1 sur la pile démarrée
# -----------------------------------------------------------------------------
$rev = (Psql "SELECT version_num FROM alembic_version") -join ""
Result "migration_0004_identity_en_tete" ($rev -eq "0004_identity") $rev
$r = Run "Vérification du schéma (database/checks/verify_head.sql)" {
    Get-Content "$root\database\checks\verify_head.sql" -Raw | docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -v ON_ERROR_STOP=1
}
Result "schema_tete_verifie" ($r.Code -eq 0)

# Garde de rôles en base : une insertion directe d'un rôle de plateforme sans auteur habilité est refusée.
# Transaction annulée dans tous les cas (ROLLBACK) : aucune donnée n'est modifiée.
$guard = (Psql "BEGIN; INSERT INTO user_roles (user_id, role_id) SELECT u.id, r.id FROM users u, roles r WHERE r.code = 'admin' LIMIT 1; ROLLBACK;") -join " "
Result "garde_base_role_plateforme" ($guard -match "ERROR|ERREUR") $guard

$health = try { (Invoke-RestMethod -TimeoutSec 10 -Uri "$mailpit/api/v1/info").Version } catch { "" }
Result "mailpit_api_joignable" ([bool]$health) "Mailpit $health"

$email = "validation-2-1-$stamp@example.test"
$password = "validation-phase21-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$reg1 = CallApi POST "/api/v1/auth/register" @{ email = $email; password = $password; display_name = "Validation 2.1"; locale = "fr"; adult_attestation = $true; accept_terms = $true; accept_privacy = $true }
Result "inscription_202" ($reg1.Status -eq 202) "HTTP $($reg1.Status)"
$esc = CallApi POST "/api/v1/auth/register" @{ email = "x$email"; password = $password; display_name = "X"; locale = "fr"; adult_attestation = $true; accept_terms = $true; accept_privacy = $true; role = "admin" }
Result "inscription_role_impose_refuse" ($esc.Status -eq 422) "HTTP $($esc.Status)"
$dup = CallApi POST "/api/v1/auth/register" @{ email = $email; password = $password; display_name = "Bis"; locale = "fr"; adult_attestation = $true; accept_terms = $true; accept_privacy = $true }
Result "inscription_doublon_non_divulgue" ($dup.Status -eq 202) "HTTP $($dup.Status)"
$early = CallApi POST "/api/v1/auth/login" @{ email = $email; password = $password }
Result "connexion_refusee_avant_verification" ($early.Status -eq 403) "HTTP $($early.Status)"

$vtoken = MailToken $email "/verification?jeton="
Result "courriel_verification_recu_mailpit" ([bool]$vtoken)
$ver = CallApi POST "/api/v1/auth/verify-email" @{ token = "$vtoken" }
Result "verification_adresse" ($ver.Status -eq 200) "HTTP $($ver.Status)"
$ver2 = CallApi POST "/api/v1/auth/verify-email" @{ token = "$vtoken" }
Result "jeton_verification_usage_unique" ($ver2.Status -eq 400) "HTTP $($ver2.Status)"

$bad = CallApi POST "/api/v1/auth/login" @{ email = $email; password = "mauvais-mot-de-passe-xyz" }
Result "connexion_mauvais_mot_de_passe_401" ($bad.Status -eq 401) "HTTP $($bad.Status)"
$login = CallApi POST "/api/v1/auth/login" @{ email = $email; password = $password }
$session = if ($login.Data) { "$($login.Data.session_token)" } else { "" }
Result "connexion" ($login.Status -eq 200 -and $session.Length -ge 40) "HTTP $($login.Status)"
$me = CallApi GET "/api/v1/me" $null $session
$roles = if ($me.Data) { ($me.Data.roles | ForEach-Object { $_.role }) -join "," } else { "" }
Result "role_etudiant_seulement" ($roles -eq "student") "rôles=$roles"
$cookieOnly = CallApi GET "/api/v1/me" $null "" @{ Cookie = "nl_session=$session" }
Result "api_ignore_les_cookies" ($cookieOnly.Status -eq 401) "HTTP $($cookieOnly.Status)"
$inv = CallApi POST "/api/v1/invitations" @{ email = "y$email"; role = "org_admin"; organization_id = $null; locale = "fr" } $session
Result "etudiant_ne_peut_pas_inviter" ($inv.Status -eq 403 -or $inv.Status -eq 404 -or $inv.Status -eq 422) "HTTP $($inv.Status)"
$stored = (Psql "SELECT count(*) FROM auth_sessions WHERE refresh_token_hash = '$session'") -join ""
Result "jeton_session_non_stocke_en_clair" ($stored -eq "0") "occurrences=$stored"

$forgot = CallApi POST "/api/v1/auth/password/forgot" @{ email = $email }
$rtoken = MailToken $email "/reinitialisation?jeton="
Result "courriel_reinitialisation_recu" ($forgot.Status -eq 202 -and [bool]$rtoken) "HTTP $($forgot.Status)"
$newPassword = "nouvelle-validation-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$reset = CallApi POST "/api/v1/auth/password/reset" @{ token = "$rtoken"; new_password = $newPassword }
Result "reinitialisation_mot_de_passe" ($reset.Status -eq 200) "HTTP $($reset.Status)"
$after = CallApi GET "/api/v1/me" $null $session
Result "sessions_revoquees_apres_reinitialisation" ($after.Status -eq 401) "HTTP $($after.Status)"
$login2 = CallApi POST "/api/v1/auth/login" @{ email = $email; password = $newPassword }
$session2 = if ($login2.Data) { "$($login2.Data.session_token)" } else { "" }
$out = CallApi POST "/api/v1/auth/logout" $null $session2
$after2 = CallApi GET "/api/v1/me" $null $session2
Result "deconnexion_revoque_la_session" ($login2.Status -eq 200 -and $out.Status -eq 204 -and $after2.Status -eq 401) "connexion $($login2.Status), déconnexion $($out.Status), puis $($after2.Status)"

# Outils d'exploitation (CLI) : création idempotente d'un établissement de validation.
$r = Run "CLI : python -m app.cli create-organization" {
    docker compose exec -T api python -m app.cli create-organization validation-phase-2-1 "Validation Phase 2.1" --kind school
}
Result "cli_creation_etablissement" ($r.Code -eq 0)

# Journal d'audit : événements présents, aucun secret.
$actions = (Psql "SELECT string_agg(DISTINCT action, ',' ORDER BY action) FROM audit_logs WHERE entity_id = (SELECT id::text FROM users WHERE email = '$email')") -join ""
$need = 'user.register', 'user.email_verified', 'auth.login', 'auth.login_failed', 'password.reset_requested', 'password.reset', 'auth.logout'
$missing = $need | Where-Object { $actions -notmatch [regex]::Escape($_) }
Result "evenements_audites" ($missing.Count -eq 0) "présents=$actions manquants=$($missing -join ',')"
$secretHits = 0
foreach ($s in @($password, $newPassword, $session, $session2, "$vtoken", "$rtoken")) {
    if (-not $s) { continue }
    $n = (Psql "SELECT count(*) FROM audit_logs WHERE metadata::text LIKE '%' || '$s' || '%'") -join ""
    if ($n -ne "0") { $secretHits++ }
}
Result "audit_sans_secret" ($secretHits -eq 0) "$secretHits secret(s) trouvé(s)"
$logs = (docker compose logs --no-color api web 2>&1 | Out-String)
$logHits = 0
foreach ($s in @($password, $newPassword, $session, $session2, "$vtoken", "$rtoken")) { if ($s -and $logs.Contains($s)) { $logHits++ } }
Result "journaux_conteneurs_sans_secret" ($logHits -eq 0) "$logHits secret(s) trouvé(s)"
$tamper = (Psql "BEGIN; UPDATE audit_logs SET action = 'x' WHERE id = (SELECT max(id) FROM audit_logs); ROLLBACK;") -join " "
Result "audit_immuable" ($tamper -match "ERROR|ERREUR") $tamper

# Pages Web d'identité (FR/EN) et Docker toujours sain.
foreach ($u in "/fr/inscription", "/en/connexion", "/fr/mot-de-passe-oublie", "/en/conditions", "/fr/confidentialite") {
    $code = try { [int](Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Uri "$web$u").StatusCode } catch { 0 }
    Result ("web_page_" + ($u -replace '[^a-z0-9]', '_')) ($code -eq 200) "HTTP $code"
}
$code = try { [int](Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Uri "$web/fr/compte" -MaximumRedirection 0).StatusCode } catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
Result "web_compte_exige_une_session" ($code -ge 300 -and $code -lt 400) "HTTP $code"
$states = @{}
foreach ($s in 'postgres', 'redis', 's3', 'api', 'web') { $states[$s] = docker inspect -f "{{.State.Health.Status}}" (docker compose ps -q $s) 2>$null }
$mp = docker inspect -f "{{.State.Status}}" (docker compose ps -q mailpit) 2>$null
$healthy = ($states.Values | Where-Object { $_ -ne "healthy" }).Count -eq 0 -and $mp -eq "running"
Result "docker_services_operationnels" $healthy (($states.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " ")

# Aucun contenu médical publié (le pilote PAB Québec reste en brouillon).
$pub = (Psql "SELECT count(*) FROM programs WHERE status = 'published'") -join ""
Result "aucun_contenu_medical_publie" ($pub -eq "0") "programmes publiés=$pub"

Log ""; Log "================ SYNTHÈSE PHASE 2.1 ================"
$fail = 0
foreach ($k in $results.Keys) { $tag = if ($results[$k]) { "PASS" } else { "FAIL"; $fail++ }; Log ("{0,-48} {1}" -f $k, $tag) }
Log ("Total : {0} contrôles Phase 2.1 (+ non-régression Phase 1.2), {1} échec(s)" -f $results.Count, $fail)
Log "Journal : $log"
Log "Courriels locaux : $mailpit  ·  Application : $web/fr/inscription"
if ($fail -gt 0) { exit 1 } else { exit 0 }

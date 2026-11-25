# =============================================================================
# NurseLearn AI — Sauvegarde chiffrée et restauration ISOLÉE des médias protégés (2.2.3-e, D22)
# Usage (PowerShell, racine du dépôt C:\NurseLearnAI-git, pile Docker démarrée) :
#   powershell -ExecutionPolicy Bypass -File .\scripts\backup-restore-media.ps1
#
# 1. clé BACKUP_ENCRYPTION_KEY (.env local, 32 octets aléatoires, créée si absente, jamais affichée) ;
# 2. image d'outillage nurselearn-backup-tools (client PostgreSQL 16, Python) ;
# 3. SAUVEGARDE : base (pg_dump --create), objets de quarantaine et sains (identités R3 de LECTURE, jamais
#    la clé racine), métadonnées, rôles sans mot de passe → archive chiffrée AES-256-GCM + manifeste,
#    dans backups\ (hors Git) ;
# 4. VÉRIFICATIONS : empreinte et déchiffrement authentifié ; altération et mauvaise clé REFUSÉES ;
# 5. RESTAURATION ISOLÉE : PostgreSQL et RustFS JETABLES (stockage en mémoire, aucun volume), réseau
#    Docker INTERNE dédié ; la base de travail n'est jamais visée ; vérification des tables, des
#    rattachements, des empreintes des objets, des états antivirus, des verdicts et des permissions ;
# 6. démontage des conteneurs et du réseau jetables ; contrôle : aucun volume créé ni supprimé, base de
#    travail inchangée. Résultat JSON : docs\validation-logs\backup-restore-<date>.json (hors Git).
# Ne supprime aucun volume ni aucune donnée. Aucun service payant.
# =============================================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = (Get-Location).Path
if (-not (Test-Path "$root\compose.yaml")) { Write-Error "Lancer depuis la racine du dépôt."; exit 2 }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = Join-Path $root "docs\validation-logs"
New-Item -ItemType Directory -Force $logDir, (Join-Path $root "backups") | Out-Null
$out = Join-Path $logDir "backup-restore-$stamp.json"
function ReadEnv { $v = @{}; Get-Content "$root\.env" | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object { $k, $x = $_ -split '=', 2; $v[$k.Trim()] = $x.Trim() }; return $v }
function Hex([int]$n) { $b = New-Object byte[] $n; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString("x2") }) }
function LastJson([string]$raw) { try { ($raw -split "`n" | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json } catch { $null } }

$envVars = ReadEnv
if (-not $envVars['BACKUP_ENCRYPTION_KEY']) {
    Add-Content -Path "$root\.env" -Value "`r`nBACKUP_ENCRYPTION_KEY=$(Hex 32)" -Encoding ASCII
    Write-Host "BACKUP_ENCRYPTION_KEY absente de .env : clé aléatoire créée (non affichée). Conservez-la hors du poste : sans elle, aucune restauration."
    $envVars = ReadEnv
}
$key = $envVars['BACKUP_ENCRYPTION_KEY']
$PGU = if ($envVars['POSTGRES_USER']) { $envVars['POSTGRES_USER'] } else { "nurselearn" }
$PGDB = if ($envVars['POSTGRES_DB']) { $envVars['POSTGRES_DB'] } else { "nurselearn" }
$PGPW = $envVars['POSTGRES_PASSWORD']
function Psql([string]$sql) { (docker compose exec -T postgres psql -U $PGU -d $PGDB -X -q -At -c $sql 2>&1 | ForEach-Object { "$_" }) -join " " }
$COUNTS = "SELECT (SELECT count(*) FROM users) || ' ' || (SELECT count(*) FROM media_assets) || ' ' || (SELECT count(*) FROM lesson_version_media) || ' ' || (SELECT count(*) FROM media_scan_events) || ' ' || (SELECT count(*) FROM media_legal_hold_events) || ' ' || (SELECT count(*) FROM audit_logs)"
$result = [ordered]@{ stamp = $stamp }

$volsBefore = @(docker volume ls -q 2>$null | Sort-Object)
$countsBefore = Psql $COUNTS
Write-Host "Image d'outillage nurselearn-backup-tools…"
docker build -q -t nurselearn-backup-tools -f "$root\infra\backup\Dockerfile" "$root" 2>&1 | Out-Null
$result.image = [bool](docker image inspect nurselearn-backup-tools --format "{{.Id}}" 2>$null)

# --- 3. Sauvegarde ---------------------------------------------------------------------------
Write-Host "Sauvegarde chiffrée…"
$bk = (docker run --rm --network nurselearn_default --tmpfs /work:size=1g,mode=1777 -v "${root}\backups:/backup" `
    -e BACKUP_ENCRYPTION_KEY=$key -e SRC_HOST=postgres -e SRC_USER=$PGU -e SRC_PASSWORD=$PGPW -e SRC_DB=$PGDB `
    -e S3_ENDPOINT=http://s3:9000 -e "S3_SCANNER_SECRET_KEY=$($envVars['S3_SCANNER_SECRET_KEY'])" -e "S3_READER_SECRET_KEY=$($envVars['S3_READER_SECRET_KEY'])" `
    nurselearn-backup-tools backup --out /backup --stamp $stamp 2>&1 | Out-String)
$b = LastJson $bk
$result.backup = $b
if (-not $b -or -not $b.ok) {
    $result.erreur = (($bk -split "`n" | Select-Object -Last 4) -join " ").Replace($key, "***").Replace("$PGPW", "***")
    $result | ConvertTo-Json -Depth 6 | Set-Content $out -Encoding UTF8; Write-Host "Échec de la sauvegarde : $out"; exit 1
}
$manifest = "/backup/$($b.manifest)"
$archive = Join-Path $root "backups\$($b.archive)"
git check-ignore -q "backups/$($b.archive)" 2>$null | Out-Null
$result.archive_hors_git = ($LASTEXITCODE -eq 0)
$result.cle_absente_du_manifeste = -not ((Get-Content (Join-Path $root "backups\$($b.manifest)") -Raw).Contains($key))

# --- 4. Vérifications d'intégrité --------------------------------------------------------------
Write-Host "Vérifications d'intégrité…"
$verify = @{}
foreach ($mode in @("normal", "tamper", "wrong-key")) {
    $flag = @(); if ($mode -eq "tamper") { $flag = @("--tamper") } elseif ($mode -eq "wrong-key") { $flag = @("--wrong-key") }
    $raw = (docker run --rm --network none -v "${root}\backups:/backup:ro" -e BACKUP_ENCRYPTION_KEY=$key nurselearn-backup-tools verify --manifest $manifest @flag 2>&1 | Out-String)
    $verify[$mode] = LastJson $raw
}
$result.verification = $verify

# --- 5. Restauration ISOLÉE ---------------------------------------------------------------------
Write-Host "Restauration isolée (PostgreSQL et RustFS jetables, réseau interne)…"
$net = "nl-restore-$stamp"; $pg = "nl-restore-pg-$stamp"; $s3 = "nl-restore-s3-$stamp"
$rpw = Hex 16; $rsk = Hex 16
docker network create --internal $net 2>&1 | Out-Null
docker run -d --name $pg --network $net --tmpfs /var/lib/postgresql/data:size=2g -e POSTGRES_USER=nl_restore -e POSTGRES_PASSWORD=$rpw pgvector/pgvector:pg16 2>&1 | Out-Null
docker run -d --name $s3 --network $net --tmpfs /data:size=4g -e RUSTFS_ACCESS_KEY=nl-restore -e RUSTFS_SECRET_KEY=$rsk -e RUSTFS_VOLUMES=/data -e RUSTFS_ADDRESS=0.0.0.0:9000 rustfs/rustfs:1.0.0 2>&1 | Out-Null
# Écoute TCP exigée : pendant l'initialisation, l'image démarre un serveur temporaire sur socket local seulement.
for ($i = 0; $i -lt 90; $i++) { docker exec $pg pg_isready -h 127.0.0.1 -U nl_restore -q 2>$null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2 }
Start-Sleep 2
$mounts = ((docker inspect -f "{{range .Mounts}}{{.Type}} {{end}}" $pg $s3 2>$null) -join " ").Trim()
$result.restauration_sans_volume = -not ($mounts -match "volume")
$result.reseau_isole = ((docker network inspect -f "{{.Internal}}" $net 2>$null) -eq "true")
$rs = (docker run --rm --network $net --tmpfs /work:size=1g,mode=1777 -v "${root}\backups:/backup:ro" -v "${root}\database\checks:/checks:ro" `
    -e BACKUP_ENCRYPTION_KEY=$key -e DST_HOST=$pg -e DST_USER=nl_restore -e DST_PASSWORD=$rpw -e DST_DB=postgres `
    -e "RESTORE_S3_ENDPOINT=http://${s3}:9000" -e RESTORE_S3_KEY=nl-restore -e RESTORE_S3_SECRET=$rsk `
    nurselearn-backup-tools restore --manifest $manifest 2>&1 | Out-String)
$result.restauration = LastJson $rs
if (-not $result.restauration) { $result.restauration_erreur = (($rs -split "`n" | Select-Object -Last 4) -join " ").Replace($key, "***") }
# La base de travail n'est pas joignable depuis le réseau isolé (preuve).
$result.base_de_travail_injoignable = ((docker run --rm --network $net --entrypoint pg_isready nurselearn-backup-tools -h postgres -t 3 2>&1 | Out-String) -notmatch "accepting connections")

# --- 6. Démontage et contrôles -------------------------------------------------------------------
docker rm -f $pg $s3 2>&1 | Out-Null
docker network rm $net 2>&1 | Out-Null
$volsAfter = @(docker volume ls -q 2>$null | Sort-Object)
$result.aucun_volume_cree_ni_supprime = (($volsBefore -join ",") -eq ($volsAfter -join ","))
$countsAfter = Psql $COUNTS
$result.base_de_travail_inchangee = ($countsBefore -eq $countsAfter)
$result.compteurs_travail = "$countsBefore"
$ok = $b.ok -and $verify['normal'].ok -and (-not $verify['tamper'].ok) -and (-not $verify['wrong-key'].ok) -and $result.restauration.ok `
    -and $result.restauration_sans_volume -and $result.reseau_isole -and $result.base_de_travail_injoignable -and $result.aucun_volume_cree_ni_supprime `
    -and $result.base_de_travail_inchangee -and $result.archive_hors_git -and $result.cle_absente_du_manifeste
$result.ok = [bool]$ok
$json = ($result | ConvertTo-Json -Depth 6).Replace($key, "***")
Set-Content -Path $out -Value $json -Encoding UTF8
Write-Host "Sauvegarde : backups\$($b.archive) ($([math]::Round($b.archive_bytes / 1MB, 1)) Mio) ; restauration isolée : $(if ($result.restauration.ok) { 'conforme' } else { 'NON conforme' })"
Write-Host "Résultat : $out"
if ($ok) { exit 0 } else { exit 1 }

# Applique le schéma et les données de référence dans le conteneur PostgreSQL local.
# Usage (PowerShell, à la racine du projet) :  .\scripts\db-apply.ps1
# Phase 1 uniquement : à partir de la Phase 2, utiliser "alembic upgrade head".
$ErrorActionPreference = "Stop"
$compose = @("compose", "-f", "infra/docker-compose.yml", "--env-file", ".env")
$envFile = Get-Content .env | Where-Object { $_ -match "^\s*[A-Z_]+=" }
$vars = @{}; foreach ($l in $envFile) { $k, $v = $l -split "=", 2; $vars[$k.Trim()] = $v.Trim() }
$user = $vars["POSTGRES_USER"]; $db = $vars["POSTGRES_DB"]

foreach ($f in @("/database/schema/0001_core.sql", "/database/schema/0002_pgvector.sql", "/database/schema/0003_business_rules.sql", "/database/schema/0004_identity.sql", "/database/seeds/0001_reference_data.sql")) {
    Write-Host "-> $f"
    docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $user -d $db -f $f
    if ($LASTEXITCODE -ne 0) { throw "Échec sur $f" }
}
Write-Host "Base prête."

# Lance les tests du schéma contre le PostgreSQL du docker-compose.
# Prérequis : Python 3.10+, pytest (pip install pytest) et psql dans le PATH.
$ErrorActionPreference = "Stop"
$vars = @{}; Get-Content .env | Where-Object { $_ -match "^\s*[A-Z_]+=" } | ForEach-Object { $k, $v = $_ -split "=", 2; $vars[$k.Trim()] = $v.Trim() }
$env:NL_TEST_PGHOST = "localhost"
$env:NL_TEST_PGPORT = $vars["POSTGRES_PORT"]
$env:NL_TEST_PGUSER = $vars["POSTGRES_USER"]
$env:PGPASSWORD = $vars["POSTGRES_PASSWORD"]
python -m pytest -q database/tests services/api/tests

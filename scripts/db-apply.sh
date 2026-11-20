#!/usr/bin/env bash
# Équivalent Linux/macOS de db-apply.ps1. Phase 1 uniquement.
set -euo pipefail
set -a; source .env; set +a
for f in /database/schema/0001_core.sql /database/schema/0002_pgvector.sql /database/schema/0003_business_rules.sql /database/schema/0004_identity.sql /database/schema/0005_learning_engine.sql /database/seeds/0001_reference_data.sql; do
  echo "-> $f"
  docker compose -f infra/docker-compose.yml --env-file .env exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$f"
done
echo "Base prête."

"""D14 : séparation des rôles PostgreSQL (propriétaire nl_owner, API nl_api, worker nl_media_scanner).

Revision ID: 0009_postgres_role_separation
Revises: 0008_media_scanner_role
Create Date: 2026-11-22
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0009_postgres_role_separation"
down_revision = "0008_media_scanner_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0009_postgres_role_separation.sql")


def downgrade() -> None:
    run_sql("0009_postgres_role_separation_down.sql")

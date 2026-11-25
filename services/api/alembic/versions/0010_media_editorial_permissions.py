"""2.2.3-e (E1) : rattachement contrôlé des médias (D3-3) et conservation légale (media:legal_hold).

Revision ID: 0010_media_editorial_permissions
Revises: 0009_postgres_role_separation
Create Date: 2026-11-24
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0010_media_editorial_permissions"
down_revision = "0009_postgres_role_separation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0010_media_editorial_permissions.sql")


def downgrade() -> None:
    run_sql("0010_media_editorial_permissions_down.sql")

"""Rôle de service media_scanner et connexion PostgreSQL nl_media_scanner (étape 2.2.3-c).

Revision ID: 0008_media_scanner_role
Revises: 0007_secure_media_foundation
Create Date: 2026-11-22
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0008_media_scanner_role"
down_revision = "0007_secure_media_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0008_media_scanner_role.sql")


def downgrade() -> None:
    run_sql("0008_media_scanner_role_down.sql")

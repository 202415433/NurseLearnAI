"""Fondations des médias protégés (étape 2.2.3-a, ADR-0011, D7, D15–D24).

Revision ID: 0007_secure_media_foundation
Revises: 0006_independent_versions
Create Date: 2026-11-21
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

# Identifiant ≤ 32 caractères : alembic_version.version_num est un varchar(32).
revision = "0007_secure_media_foundation"
down_revision = "0006_independent_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0007_secure_media_foundation.sql")


def downgrade() -> None:
    run_sql("0007_secure_media_foundation_down.sql")

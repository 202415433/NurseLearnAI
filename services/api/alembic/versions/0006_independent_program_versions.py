"""Versions de modules indépendantes par édition de programme (D12, ADR-0010, étape 2.2.2-a).

Revision ID: 0006_independent_program_versions
Revises: 0005_learning_engine
Create Date: 2026-11-19
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0006_independent_program_versions"
down_revision = "0005_learning_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0006_independent_program_versions.sql")


def downgrade() -> None:
    run_sql("0006_independent_program_versions_down.sql")

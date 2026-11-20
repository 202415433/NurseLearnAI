"""Moteur pédagogique : éditions de programme, workflow éditorial, inscriptions (Phase 2.2.1).

Revision ID: 0005_learning_engine
Revises: 0004_identity
Create Date: 2026-11-19
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0005_learning_engine"
down_revision = "0004_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0005_learning_engine.sql")


def downgrade() -> None:
    run_sql("0005_learning_engine_down.sql")

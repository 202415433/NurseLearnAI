"""Schéma initial : identité, catalogue, évaluations, simulations, IA, facturation, audit.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18
"""
import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0001_core.sql")


def downgrade() -> None:
    run_sql("0001_core_down.sql")

"""Règles métier : relecteurs et formateurs habilités, isolation des établissements.

Revision ID: 0003_business_rules
Revises: 0002_pgvector
Create Date: 2026-11-18
"""
import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0003_business_rules"
down_revision = "0002_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0003_business_rules.sql")


def downgrade() -> None:
    run_sql("0003_business_rules_down.sql")

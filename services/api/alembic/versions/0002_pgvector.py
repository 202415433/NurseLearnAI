"""Index vectoriel pgvector pour le RAG du tuteur IA.

Revision ID: 0002_pgvector
Revises: 0001_initial_schema
Create Date: 2026-11-18
"""
import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0002_pgvector"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0002_pgvector.sql")


def downgrade() -> None:
    run_sql("0002_pgvector_down.sql")

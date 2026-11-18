"""Index vectoriel pgvector pour le RAG du tuteur IA.

Revision ID: 0002_pgvector
Revises: 0001_initial_schema
Create Date: 2026-11-18
"""
from alembic import op

from _sql import read_sql

revision = "0002_pgvector"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(read_sql("0002_pgvector.sql"))


def downgrade() -> None:
    op.execute(read_sql("0002_pgvector_down.sql"))

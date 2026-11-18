"""Schéma initial : identité, catalogue, évaluations, simulations, IA, facturation, audit.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18
"""
from alembic import op

from _sql import read_sql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(read_sql("0001_core.sql"))


def downgrade() -> None:
    op.execute(read_sql("0001_core_down.sql"))

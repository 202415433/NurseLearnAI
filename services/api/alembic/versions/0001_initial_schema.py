"""Schéma initial : identité, catalogue, évaluations, simulations, IA, facturation, audit.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18
"""
from _sql import run_sql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0001_core.sql")


def downgrade() -> None:
    run_sql("0001_core_down.sql")

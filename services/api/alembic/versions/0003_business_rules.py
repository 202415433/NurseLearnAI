"""Règles métier : relecteurs et formateurs habilités, isolation des établissements.

Revision ID: 0003_business_rules
Revises: 0002_pgvector
Create Date: 2026-11-18
"""
from _sql import run_sql

revision = "0003_business_rules"
down_revision = "0002_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0003_business_rules.sql")


def downgrade() -> None:
    run_sql("0003_business_rules_down.sql")

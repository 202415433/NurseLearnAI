"""Identité et accès : sessions, jetons à usage unique, invitations, garde des rôles.

Revision ID: 0004_identity
Revises: 0003_business_rules
Create Date: 2026-11-19
"""

import sys
from pathlib import Path

# nl_sql.py est dans alembic/ (hors de versions/, qu'Alembic réserve aux révisions).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nl_sql import run_sql  # noqa: E402

revision = "0004_identity"
down_revision = "0003_business_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0004_identity.sql")


def downgrade() -> None:
    run_sql("0004_identity_down.sql")

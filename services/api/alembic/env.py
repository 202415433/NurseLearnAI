"""Environnement Alembic de NurseLearn AI.

Phase 1 : les migrations exécutent les fichiers SQL versionnés de /database/schema,
qui sont la source de vérité testée sur PostgreSQL.
Phase 2 : target_metadata pointera vers les modèles SQLAlchemy (app.db.base.Base.metadata)
et un test vérifiera que modèles et schéma ne divergent pas (alembic check).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Les révisions importent le module utilitaire _sql.py situé dans versions/.
sys.path.insert(0, str(Path(__file__).resolve().parent / "versions"))

config = context.config
target_metadata = None


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL n'est pas défini (voir .env.example)")
    # Alembic utilise le pilote synchrone ; l'API utilise asyncpg.
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

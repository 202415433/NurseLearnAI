"""Lecture et exécution des fichiers SQL versionnés partagés avec /database/schema.

Ce module est volontairement HORS de alembic/versions/ : Alembic considère tout
fichier .py de ce dossier comme une révision (anomalie A10, Phase 1.1).
"""
from __future__ import annotations

import os
from pathlib import Path

# Dans le conteneur Docker, /database est monté ; en local on remonte l'arborescence.
SCHEMA_DIR = Path(os.environ.get("NL_SCHEMA_DIR", Path(__file__).resolve().parents[3] / "database" / "schema"))


def read_sql(name: str) -> str:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Fichier de migration introuvable : {path}")
    return path.read_text(encoding="utf-8")


def run_sql(name: str) -> None:
    """Exécute un fichier SQL multi-instructions sur la connexion de la migration.

    Le script est envoyé directement au pilote (psycopg 3), SANS paramètres :
    - pas d'interprétation des `%` (présents dans format() et RAISE) ;
    - plusieurs instructions acceptées dans un même appel.
    `op.execute()` et même `exec_driver_sql()` passent un jeu de paramètres vide
    au pilote, qui interprète alors les `%` (anomalies A1 puis A13, Phase 1.1).
    La transaction reste celle d'Alembic (même connexion).
    """
    from alembic import op

    dbapi_conn = op.get_bind().connection.dbapi_connection
    with dbapi_conn.cursor() as cur:
        cur.execute(read_sql(name))

"""Lecture des fichiers SQL versionnés partagés avec /database/schema."""
from __future__ import annotations

import os
from pathlib import Path

# Dans le conteneur Docker, /database est monté ; en local on remonte l'arborescence.
SCHEMA_DIR = Path(os.environ.get("NL_SCHEMA_DIR", Path(__file__).resolve().parents[4] / "database" / "schema"))


def read_sql(name: str) -> str:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Fichier de migration introuvable : {path}")
    return path.read_text(encoding="utf-8")

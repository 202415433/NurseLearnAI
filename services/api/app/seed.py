"""Applique les données de référence (database/seeds/*.sql, idempotentes), dans l'ordre.

Usage : python -m app.seed
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg

from app.core.config import get_settings
from app.core.logging import configure_logging

SEEDS_DIR = Path(os.environ.get("NL_SEEDS_DIR", Path(__file__).resolve().parents[3] / "database" / "seeds"))


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("nurselearn.seed")
    files = sorted(SEEDS_DIR.glob("*.sql"))
    if not files:
        log.error("aucun_fichier_de_donnees", extra={"dir": str(SEEDS_DIR)})
        return 1
    dsn = settings.sync_database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn:
        for f in files:
            with conn.cursor() as cur:
                cur.execute(f.read_text(encoding="utf-8"))  # sans paramètres : multi-instructions, % non interprétés
            log.info("donnees_appliquees", extra={"file": f.name})
        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())

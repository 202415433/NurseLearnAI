"""Applique les données de référence (database/seeds/*.sql, idempotentes), dans l'ordre.

Usage : python -m app.seed

Si MEDIA_SCANNER_DB_PASSWORD est défini (Docker Compose, profil `media`), ouvre aussi la connexion
de service PostgreSQL `nl_media_scanner` (migration 0008) avec ce mot de passe. Le secret vient de
l'environnement (.env, non versionné) et n'est jamais journalisé.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

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
        scanner_password = os.environ.get("MEDIA_SCANNER_DB_PASSWORD", "")
        if scanner_password:
            if len(scanner_password) < 16:
                log.error("mot_de_passe_service_trop_court")
                return 1
            with conn.cursor() as cur:
                stmt = sql.SQL("ALTER ROLE nl_media_scanner LOGIN PASSWORD {}").format(sql.Literal(scanner_password))
                cur.execute(stmt)
            log.info("connexion_service_analyse_ouverte")
        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())

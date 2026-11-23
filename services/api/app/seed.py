"""Applique les données de référence (database/seeds/*.sql, idempotentes), dans l'ordre.

Usage : python -m app.seed

Si API_DB_PASSWORD ou MEDIA_SCANNER_DB_PASSWORD sont définis (conteneur `migrate` de Docker Compose),
ouvre aussi les connexions PostgreSQL `nl_api` (D14, migration 0009) et `nl_media_scanner`
(migration 0008) avec ces mots de passe. Exige la connexion d'administration. Les secrets viennent de
l'environnement (.env, non versionné) et ne sont jamais journalisés.
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
        # Connexions applicatives (D14, migrations 0008 et 0009) : mots de passe posés par
        # l'administration, depuis l'environnement du seul conteneur de migration.
        for env_name, role in (("API_DB_PASSWORD", "nl_api"), ("MEDIA_SCANNER_DB_PASSWORD", "nl_media_scanner")):
            password = os.environ.get(env_name, "")
            if not password:
                continue
            if len(password) < 16:
                log.error("mot_de_passe_connexion_trop_court", extra={"role": role})
                return 1
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cur.fetchone() is None:
                    continue  # rôle pas encore créé (base antérieure à sa migration)
                stmt = sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
                cur.execute(stmt)
            log.info("connexion_applicative_ouverte", extra={"role": role})
        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())

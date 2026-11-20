"""Fixtures de test du schéma : base PostgreSQL jetable, pilotée par psql.

Variables d'environnement :
  NL_TEST_PGHOST (défaut localhost), NL_TEST_PGPORT (5432), NL_TEST_PGUSER (postgres),
  PGPASSWORD si nécessaire.
Chaque session de test crée une base `nl_schema_test_<pid>` et la supprime à la fin.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema"
SEEDS = ROOT / "seeds"


class SqlError(RuntimeError):
    pass


class Db:
    def __init__(self, name: str):
        self.name = name
        self.base = [
            "psql",
            "-h",
            os.environ.get("NL_TEST_PGHOST", "localhost"),
            "-p",
            os.environ.get("NL_TEST_PGPORT", "5432"),
            "-U",
            os.environ.get("NL_TEST_PGUSER", "postgres"),
            "-v",
            "ON_ERROR_STOP=1",
            "-X",
            "-q",
            "-At",
        ]

    def _run(self, args: list[str], db: str | None = None) -> str:
        proc = subprocess.run(self.base + ["-d", db or self.name] + args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SqlError(proc.stderr.strip())
        return proc.stdout.strip()

    def sql(self, query: str) -> str:
        return self._run(["-c", query])

    def file(self, path: Path) -> str:
        return self._run(["-f", str(path)])

    def admin(self, query: str) -> str:
        return self._run(["-c", query], db="postgres")

    def fails(self, query: str) -> str:
        """Exécute une requête qui DOIT échouer ; renvoie le message d'erreur."""
        try:
            self.sql(query)
        except SqlError as exc:
            return str(exc)
        raise AssertionError(f"La requête aurait dû être rejetée :\n{query}")


@pytest.fixture(scope="session")
def db():
    d = Db(f"nl_schema_test_{os.getpid()}")
    d.admin(f"DROP DATABASE IF EXISTS {d.name}")
    d.admin(f"CREATE DATABASE {d.name}")
    d.file(SCHEMA / "0001_core.sql")
    if d.sql("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'") == "1":
        d.file(SCHEMA / "0002_pgvector.sql")
    d.file(SCHEMA / "0003_business_rules.sql")  # indépendante des objets de 0002
    d.file(SCHEMA / "0004_identity.sql")
    for seed in sorted(SEEDS.glob("*.sql")):
        d.file(seed)
    yield d
    d.admin(f"DROP DATABASE IF EXISTS {d.name} WITH (FORCE)")


def new_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def make_user(db):
    def _make(email: str | None = None) -> str:
        uid = new_id()
        email = email or f"{uid[:8]}@example.test"
        db.sql(f"INSERT INTO users (id, email, status, email_verified_at) VALUES ('{uid}', '{email}', 'active', now())")
        return uid

    return _make


@pytest.fixture
def grant_role(db):
    """Attribue un rôle (global si org est None) à un utilisateur.

    Données de test : la garde d'attribution (migration 0004) est contournée explicitement,
    comme le ferait la commande d'exploitation ; ses règles sont testées à part.
    """

    def _grant(user_id: str, role_code: str, org: str | None = None) -> None:
        org_sql = f"'{org}'" if org else "NULL"
        db.sql(
            "BEGIN; SELECT set_config('nl.role_grant_bypass', 'on', true); "
            f"INSERT INTO user_roles (user_id, role_id, organization_id) "
            f"SELECT '{user_id}', id, {org_sql} FROM roles WHERE code = '{role_code}'; COMMIT;"
        )

    return _grant


@pytest.fixture
def make_reviewer(make_user, grant_role):
    def _make() -> str:
        uid = make_user()
        grant_role(uid, "medical_reviewer")
        return uid

    return _make


@pytest.fixture
def make_org(db):
    def _make() -> str:
        oid = new_id()
        db.sql(
            f"INSERT INTO organizations (id, slug, name, kind) VALUES ('{oid}', 'org-{oid[:8]}', 'Institut {oid[:4]}', 'school')"
        )
        return oid

    return _make


@pytest.fixture
def specialty_id(db):
    return db.sql("SELECT id FROM specialties WHERE code = 'care_assistant_core'")


@pytest.fixture
def make_lesson(db, specialty_id):
    def _make() -> str:
        mid, lid = new_id(), new_id()
        db.sql(
            f"INSERT INTO modules (id, code, specialty_id, level, title_i18n) VALUES "
            f"('{mid}', 'm-{mid[:8]}', '{specialty_id}', 'beginner', '{{\"fr\":\"Hygiène des mains\"}}')"
        )
        db.sql(
            f"INSERT INTO lessons (id, module_id, code, position, kind) VALUES ('{lid}', '{mid}', 'l1', 0, 'theory')"
        )
        return lid

    return _make

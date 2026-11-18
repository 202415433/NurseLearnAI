"""Vérifie la chaîne de révisions Alembic SANS exécuter Alembic (substitut).

ATTENTION : ce test valide le SQL émis par chaque révision, pas l'outil Alembic
lui-même. L'exécution réelle (`alembic upgrade head` / `downgrade base`) est faite
par `scripts/validate-phase1.1.ps1` et par la CI (job « Alembic réel »).

Un faux module `alembic.op` (get_bind().exec_driver_sql) capture le SQL émis par chaque révision ; ce SQL est
ensuite exécuté sur PostgreSQL (upgrade -> downgrade -> upgrade). La révision
0002 (pgvector) n'est exécutée que si l'extension `vector` est disponible.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
PSQL = ["psql", "-h", os.environ.get("NL_TEST_PGHOST", "localhost"),
        "-p", os.environ.get("NL_TEST_PGPORT", "5432"),
        "-U", os.environ.get("NL_TEST_PGUSER", "postgres"), "-v", "ON_ERROR_STOP=1", "-X", "-q", "-At"]


def _load_revisions():
    captured: list[str] = []
    fake_alembic = types.ModuleType("alembic")
    connection = types.SimpleNamespace(exec_driver_sql=captured.append)
    fake_alembic.op = types.SimpleNamespace(get_bind=lambda: connection)
    sys.modules["alembic"] = fake_alembic
    sys.path.insert(0, str(VERSIONS))
    revs = {}
    for f in sorted(VERSIONS.glob("0*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        revs[mod.revision] = mod
    return revs, captured


def _psql(db: str, sql: str) -> str:
    p = subprocess.run(PSQL + ["-d", db], input=sql, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()


def test_revision_chain_is_linear():
    revs, _ = _load_revisions()
    heads = [r for r in revs if not any(m.down_revision == r for m in revs.values())]
    roots = [r for r, m in revs.items() if m.down_revision is None]
    assert roots == ["0001_initial_schema"]
    assert heads == ["0003_business_rules"]
    for r, m in revs.items():
        assert m.down_revision is None or m.down_revision in revs, r


def _has_vector() -> bool:
    return _psql("postgres", "SELECT count(*) FROM pg_available_extensions WHERE name='vector'") == "1"


def test_core_upgrade_downgrade_upgrade_on_postgres():
    # Sans pgvector : 0001 puis 0003 (0003 ne dépend d'aucun objet de 0002).
    _run_chain(["0001_initial_schema", "0003_business_rules"])


def test_pgvector_revision_on_postgres():
    if not _has_vector():
        pytest.skip("Extension pgvector absente : révision 0002 vérifiée en CI (image pgvector/pgvector:pg16)")
    _run_chain(["0001_initial_schema", "0002_pgvector", "0003_business_rules"])


def _run_chain(chain: list[str]) -> None:
    revs, captured = _load_revisions()
    db = f"nl_mig_test_{os.getpid()}"
    _psql("postgres", f"DROP DATABASE IF EXISTS {db}; CREATE DATABASE {db};")
    try:
        count = "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        for r in chain:
            captured.clear(); revs[r].upgrade(); _psql(db, captured[0])
        n = _psql(db, count)
        for r in reversed(chain):
            captured.clear(); revs[r].downgrade(); _psql(db, captured[0])
        assert _psql(db, count) == "0"
        for r in chain:
            captured.clear(); revs[r].upgrade(); _psql(db, captured[0])
        assert _psql(db, count) == n
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")

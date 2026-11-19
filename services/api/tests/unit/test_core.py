"""Tests unitaires (sans base de données) des briques transverses de l'API."""

from __future__ import annotations

import json
import logging

import pytest

# Ces tests exigent les dépendances de l'API (pip install -e ".[dev]") ; ignorés sinon
# (ex. validation Phase 1.1 qui n'installe que pytest).
pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

from app.core.config import Settings  # noqa: E402
from app.core.i18n import negotiate_locale, pick  # noqa: E402
from app.core.logging import JsonFormatter  # noqa: E402
from app.core.pagination import PageParams  # noqa: E402


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, "fr"),
        ("", "fr"),
        ("en", "en"),
        ("en-CA,en;q=0.9", "en"),
        ("fr-CA,fr;q=0.9,en;q=0.8", "fr"),
        ("de-DE,en;q=0.5", "en"),
        ("de-DE", "fr"),
        ("en;q=0.2,fr;q=0.9", "fr"),
        ("en;q=abc", "fr"),
    ],
)
def test_negotiate_locale(header, expected):
    assert negotiate_locale(header, ["fr", "en"], "fr") == expected


def test_pick_falls_back_in_order():
    assert pick({"fr": "Bonjour", "en": "Hello"}, "en") == "Hello"
    assert pick({"fr": "Bonjour"}, "en") == "Bonjour"
    assert pick({"en": "Hello"}, "fr") == "Hello"
    assert pick({"es": "Hola"}, "fr") == "Hola"
    assert pick(None, "fr") is None and pick({}, "fr") is None


def test_page_slice():
    page = PageParams(limit=2, offset=1).slice(list("abcde"))
    assert page.items == ["b", "c"] and page.total == 5 and page.limit == 2 and page.offset == 1
    assert PageParams(limit=10, offset=9).slice(list("ab")).items == []


def test_database_url_drivers():
    s = Settings(database_url="postgresql://u:p@h:5432/d")  # type: ignore[call-arg]
    assert s.async_database_url == "postgresql+asyncpg://u:p@h:5432/d"
    assert s.sync_database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_json_logs_contain_no_message_args_and_are_valid_json():
    record = logging.makeLogRecord(
        {"name": "t", "levelname": "INFO", "msg": "requete", "path": "/api/v1/countries", "status": 200}
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "requete" and payload["path"] == "/api/v1/countries" and payload["status"] == 200
    assert "args" not in payload

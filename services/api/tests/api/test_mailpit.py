"""Envoi réel par SMTP vers Mailpit (aucun service payant) : inscription → courriel → vérification.

Exécuté si MAILPIT_URL est défini (CI, script de validation Docker) ; SMTP_HOST/SMTP_PORT désignent Mailpit.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

MAILPIT = os.environ.get("MAILPIT_URL")
pytestmark = pytest.mark.skipif(not MAILPIT, reason="MAILPIT_URL absent")


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{MAILPIT}{path}", timeout=5) as r:  # noqa: S310 — URL de test maîtrisée
        return json.load(r)


def _wait_for(address: str) -> str:
    query = urllib.parse.quote(f'to:"{address}"')
    for _ in range(30):
        found = _get(f"/api/v1/search?query={query}").get("messages", [])
        if found:
            return _get(f"/api/v1/message/{found[0]['ID']}")["Text"]
        time.sleep(0.5)
    raise AssertionError(f"Aucun courriel reçu par Mailpit pour {address}")


def test_verification_email_through_mailpit():
    settings = Settings(  # type: ignore[call-arg]
        mail_backend="smtp", rate_limit_per_minute=0, rate_limit_salt=uuid.uuid4().hex, register_per_hour_per_ip=10_000
    )
    address = f"mailpit-{uuid.uuid4().hex[:10]}@example.test"
    with TestClient(create_app(settings)) as c:
        r = c.post(
            "/api/v1/auth/register",
            json={
                "email": address,
                "password": "Une phrase de passe solide 2026",
                "display_name": "Test Mailpit",
                "locale": "fr",
                "adult_attestation": True,
                "accept_terms": True,
                "accept_privacy": True,
            },
        )
        assert r.status_code == 202
        body = _wait_for(address)
        assert "valable 24 heures" in body
        token = re.search(r"jeton=([A-Za-z0-9_-]{20,})", body)
        assert token, body
        assert c.post("/api/v1/auth/verify-email", json={"token": token.group(1)}).status_code == 200

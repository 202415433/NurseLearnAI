"""Fraîcheur des signatures ClamAV (2.2.3-e, E1) : signatures absentes, illisibles, périmées ou futures
ne produisent JAMAIS de verdict sain (fermé par défaut). Le seuil de 48 h ne peut pas être relevé."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.workers.clamd import ClamdError, EngineInfo, parse_version
from app.workers.media_scan import MediaScanWorker


def fresh(published: datetime | None, max_hours: float = 48) -> bool:
    worker = SimpleNamespace(s=SimpleNamespace(media_signature_max_age_hours=max_hours))
    return MediaScanWorker._fresh(worker, EngineInfo("1.5.5", "28193", published))  # type: ignore[arg-type]


def test_version_reply_parsing() -> None:
    info = parse_version("ClamAV 1.5.5/28193/Tue Nov 24 06:24:27 2026\n")
    assert (info.engine_version, info.signature_version) == ("1.5.5", "28193")
    assert info.signatures_published_at == datetime(2026, 11, 24, 6, 24, 27, tzinfo=UTC)
    # Jour sur un chiffre (double espace dans la réponse de clamd).
    assert parse_version("ClamAV 1.5.5/28190/Sun Nov  1 07:00:00 2026").signatures_published_at is not None


@pytest.mark.parametrize("reply", ["", "ClamAV 1.5.5", "PONG", "ERROR", "ClamAV 1.5.5/"])
def test_absent_signatures_are_refused(reply: str) -> None:
    with pytest.raises(ClamdError):
        parse_version(reply)


def test_unreadable_signature_date_is_never_fresh() -> None:
    info = parse_version("ClamAV 1.5.5/28193/date illisible")
    assert info.signatures_published_at is None
    assert fresh(None) is False


def test_freshness_window() -> None:
    now = datetime.now(UTC)
    assert fresh(now - timedelta(hours=1)) is True
    assert fresh(now - timedelta(hours=47, minutes=59)) is True
    assert fresh(now - timedelta(hours=48, minutes=1)) is False  # périmées : jamais « clean »
    assert fresh(now - timedelta(hours=57)) is False  # cas réel de la CI du 2026-11-24 (base embarquée)
    assert fresh(now + timedelta(minutes=10)) is False  # horloge incohérente
    assert fresh(now - timedelta(hours=1), max_hours=0) is False


def test_freshness_threshold_cannot_exceed_48_hours() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+asyncpg://x@localhost/x", media_signature_max_age_hours=49)

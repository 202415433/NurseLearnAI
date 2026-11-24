"""Worker d'analyse antivirus (2.2.3-c) sur PostgreSQL, Redis, stockage S3 et ClamAV RÉELS.

Variables :
* DATABASE_URL (connexion de l'API, superutilisateur de développement) ;
* REDIS_URL ; NL_TEST_S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY ;
* NL_TEST_CLAMD_HOST / NL_TEST_CLAMD_PORT : ClamAV réel (CI, Windows) ;
  NL_TEST_CLAMD_KIND=fake : imitation locale (mise au point sans Docker uniquement).
Le fichier EICAR est construit à l'exécution, déposé UNIQUEMENT en quarantaine de test, jamais versionné.
"""

# ruff: noqa: F811 - fixtures pytest importées puis utilisées comme paramètres
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fake_clamd import FakeClamd
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine, make_url
from test_editorial import ok, sql
from test_media_upload import (
    QUARANTINE,
    REAL_S3,
    S3,
    M,
    MWorld,
    api,  # noqa: F401
    media_settings,
    mw,  # noqa: F401
    png,
    reserve,
    s3_client,
    uploaded,
)

from app.workers.clamd import ClamdClient
from app.workers.media_scan import SERVICE_ACCOUNT, MediaScanWorker, Report, WorkerConfigError

CLEAN = "nl-test-media-clean"
CLAMD_KIND = os.environ.get("NL_TEST_CLAMD_KIND", "real")
CLAMD_HOST = os.environ.get("NL_TEST_CLAMD_HOST")
REDIS = os.environ.get("REDIS_URL")

pytestmark = pytest.mark.skipif(
    not (S3 and REDIS and (CLAMD_HOST or CLAMD_KIND == "fake")),
    # Libellé distinct de celui des tests de téléversement (que validate-phase2.2.3b.ps1 compte).
    reason="worker d'analyse : ClamAV réel non configuré (NL_TEST_CLAMD_HOST), ou Redis ou S3 absent",
)


def eicar() -> bytes:
    """Fichier de test antivirus normalisé, reconstitué à l'exécution (jamais présent tel quel dans le dépôt)."""
    return ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-" + "TEST-FILE!$H+H*").encode()


# ============================================================================================ fixtures
@pytest.fixture(scope="module")
def clamd() -> Iterator[tuple[str, int]]:
    if CLAMD_KIND == "fake":
        fake = FakeClamd()
        yield "127.0.0.1", fake.port
        fake.close()
        return
    host, port = CLAMD_HOST or "", int(os.environ.get("NL_TEST_CLAMD_PORT", "3310"))
    client = ClamdClient(host, port, 30)
    deadline = time.monotonic() + 600
    last = "aucune réponse"
    while True:  # ClamAV réel : attendre des signatures chargées ET fraîches (freshclam au démarrage)
        try:
            info = client.version()
            last = f"{info.engine_version}/{info.signature_version}/{info.signatures_published_at}"
            if info.signatures_published_at and time.time() - info.signatures_published_at.timestamp() < 47 * 3600:
                break
        except Exception as exc:  # démon encore en chargement : on réessaie (dernière cause conservée)
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() > deadline:
            pytest.fail(
                "ClamAV : signatures absentes ou de plus de 47 h après 10 min d'attente (freshclam ?) — "
                f"dernière réponse : {last}"
            )
        time.sleep(5)
    yield host, port


@pytest.fixture(scope="module")
def scanner_dsn(sync_engine: Engine) -> str:
    """Connexion de SERVICE. Pile Docker partagée : mot de passe de l'exploitation fourni et inchangé
    (NL_TEST_SCANNER_PASSWORD) ; sinon mot de passe aléatoire posé comme le ferait l'exploitation."""
    password = os.environ.get("NL_TEST_SCANNER_PASSWORD", "")
    if not password:
        password = secrets.token_urlsafe(24)
        with sync_engine.begin() as c:
            c.exec_driver_sql(f"ALTER ROLE nl_media_scanner LOGIN PASSWORD '{password}'")
    url = sync_engine.url.set(drivername="postgresql", username="nl_media_scanner", password=password)
    return url.render_as_string(hide_password=False)


def worker_settings(scanner_dsn: str, clamd: tuple[str, int], **overrides: Any) -> Any:
    base: dict[str, Any] = {
        "media_scanner_database_url": scanner_dsn,
        "clamd_host": clamd[0],
        "clamd_port": clamd[1],
        "clamd_timeout_seconds": 60,
        "redis_url": REDIS,
        "media_scan_stream": f"nl:test:media:scan:{uuid.uuid4().hex[:8]}",
        "media_scan_retry_base_seconds": 0,
        "media_scan_reconcile_seconds": 1,
    }
    base.update(overrides)
    return media_settings(**base)


@pytest.fixture
def worker(scanner_dsn: str, clamd: tuple[str, int]) -> Iterator[MediaScanWorker]:
    w = MediaScanWorker(worker_settings(scanner_dsn, clamd))
    yield w
    w.reset()


def make(scanner_dsn: str, clamd: tuple[str, int], **overrides: Any) -> MediaScanWorker:
    return MediaScanWorker(worker_settings(scanner_dsn, clamd, **overrides))


def row(engine: Engine, media_id: str) -> dict[str, Any]:
    r = sql(
        engine,
        "SELECT state, scan_attempt, clean_key, quarantine_key, sha256 FROM media_assets WHERE id = :i",
        i=media_id,
    ).one()
    return {"state": r[0], "attempt": r[1], "clean_key": r[2], "quarantine_key": r[3], "sha256": r[4]}


def events(engine: Engine, media_id: str) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in sql(
            engine,
            "SELECT attempt, result, engine_version, signature_version, threat_name, scanned_by::text "
            "FROM media_scan_events WHERE media_id = :i ORDER BY scanned_at, attempt",
            i=media_id,
        ).all()
    ]


def last_audit(engine: Engine, media_id: str, action: str) -> dict[str, Any]:
    return sql(
        engine,
        "SELECT metadata FROM audit_logs WHERE entity_id = :i AND action = :a ORDER BY id DESC LIMIT 1",
        i=media_id,
        a=action,
    ).scalar()


def png_media(api: TestClient, mw: MWorld, who: Any = None, **extra: Any) -> str:
    data = png(size=(32 + secrets.randbelow(50), 24))
    return str(uploaded(api, who or mw.author, "image", "image/png", data, **extra)["id"])


def planted(api: TestClient, mw: MWorld, engine: Engine, data: bytes, who: Any = None) -> str:
    """Média dont le contenu a échappé aux contrôles de format (cas adverse) : déposé tel quel en quarantaine.

    Réservation par l'API, puis réception déclarée par le DÉPOSANT (seule transition qu'il peut faire) :
    l'antivirus reste la dernière barrière, et le fichier ne quitte jamais la quarantaine de test.
    """
    who = who or mw.author
    media = ok(reserve(api, who, "document", "application/pdf", len(data)), 201)
    key = f"{uuid.uuid4()}/{uuid.uuid4()}"
    s3_client().put_object(Bucket=QUARANTINE, Key=key, Body=data, ContentType="application/octet-stream")
    with engine.begin() as c:
        c.exec_driver_sql("SELECT set_config('nl.actor_id', %s, true)", (who.user_id,))
        c.exec_driver_sql(
            "UPDATE media_assets SET state = 'quarantined', byte_size = %s, sha256 = %s, quarantine_key = %s WHERE id = %s",
            (len(data), hashlib.sha256(data).hexdigest(), key, media["id"]),
        )
    return str(media["id"])


def clean_keys() -> set[str]:
    try:
        pages = s3_client().get_paginator("list_objects_v2").paginate(Bucket=CLEAN)
        return {o["Key"] for p in pages for o in p.get("Contents", [])}
    except Exception:
        return set()


# =============================================================================================
# 1–2. Fichier sain validé et copié ; EICAR détecté et maintenu hors de toute diffusion
# =============================================================================================
def test_clean_file_is_validated_copied_and_traceable(api, mw, sync_engine, worker):
    mid = png_media(api, mw)
    before = row(sync_engine, mid)
    assert worker.process(mid) == "clean"
    after = row(sync_engine, mid)
    assert after["state"] == "clean" and after["attempt"] == 1 and after["clean_key"]
    assert after["clean_key"] != before["quarantine_key"]
    stored = s3_client().get_object(Bucket=CLEAN, Key=after["clean_key"])["Body"].read()
    assert hashlib.sha256(stored).hexdigest() == before["sha256"]  # octets identiques à ceux analysés
    (ev,) = events(sync_engine, mid)
    assert ev[0] == 1 and ev[1] == "clean" and ev[2] and ev[3] and ev[4] is None and ev[5] == SERVICE_ACCOUNT
    meta = last_audit(sync_engine, mid, "media.scan_clean")
    assert meta["result"] == "clean" and meta["signature_version"]
    blob = str(meta)
    for secret in (after["clean_key"], before["quarantine_key"], "fichier-fictif"):
        assert secret not in blob  # ni clé de stockage ni nom de fichier dans l'audit
    # L'API n'expose ni clé ni URL, même pour un média sain.
    body = ok(api.get(f"{M}/{mid}", headers=mw.author.h))
    assert body["state"] == "clean" and "key" not in str(body).lower() and "http" not in str(body)


def test_eicar_is_detected_and_never_copied(api, mw, sync_engine, worker):
    mid = planted(api, mw, sync_engine, eicar())
    keys_before = clean_keys()
    assert worker.process(mid) == "infected"
    r = row(sync_engine, mid)
    assert r["state"] == "infected" and r["clean_key"] is None
    (ev,) = events(sync_engine, mid)
    assert ev[1] == "infected" and "eicar" in (ev[4] or "").lower()
    assert clean_keys() == keys_before  # aucune copie saine
    assert "eicar" in last_audit(sync_engine, mid, "media.scan_infected")["threat_name"].lower()
    # Un nouveau passage ne revalide jamais un média infecté.
    assert worker.process(mid) == "skipped" and row(sync_engine, mid)["state"] == "infected"


# =============================================================================================
# 3–6. Signatures périmées, refus de forcer « clean », empreinte incorrecte, tentative perdue
# =============================================================================================
def test_stale_signatures_never_produce_clean(api, mw, sync_engine, scanner_dsn, clamd):
    mid = png_media(api, mw)
    stale = make(scanner_dsn, clamd, media_signature_max_age_hours=0)
    assert stale.process(mid) == "error"
    r = row(sync_engine, mid)
    assert r["state"] == "scan_failed" and r["clean_key"] is None
    assert last_audit(sync_engine, mid, "media.scan_failed")["reason"] == "signatures_stale"
    fresh = make(scanner_dsn, clamd)
    assert fresh.process(mid) == "clean" and row(sync_engine, mid)["attempt"] == 2


def test_clean_cannot_be_forced_outside_the_service_connection(api, mw, sync_engine, scanner_dsn):
    mid = png_media(api, mw)
    attempts = [
        "UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = %s",
        "UPDATE media_assets SET state = 'clean', clean_key = '00000000-0000-4000-8000-000000000000/00000000-0000-4000-8000-000000000000', clean_at = now() WHERE id = %s",
    ]
    for actor in (SERVICE_ACCOUNT, mw.admin.user_id, mw.author.user_id):
        for stmt in attempts:
            with pytest.raises(Exception) as exc, sync_engine.begin() as c:
                c.exec_driver_sql("SELECT set_config('nl.actor_id', %s, true)", (actor,))
                c.exec_driver_sql(stmt, (mid,))
            assert "nl_media_scanner" in str(exc.value) or "forbidden" in str(exc.value)
    # Connexion de service, mais identité usurpée : refus.
    with psycopg.connect(scanner_dsn, autocommit=True) as conn, pytest.raises(psycopg.Error), conn.transaction():
        conn.execute("SELECT set_config('nl.actor_id', %s, true)", (mw.admin.user_id,))
        conn.execute("UPDATE media_assets SET state = 'scanning', scan_attempt = 1 WHERE id = %s", (mid,))
    assert row(sync_engine, mid)["state"] == "quarantined"


def test_modified_or_deleted_object_is_never_validated(api, mw, sync_engine, worker):
    s3 = s3_client()
    modified = png_media(api, mw)
    s3.put_object(Bucket=QUARANTINE, Key=row(sync_engine, modified)["quarantine_key"], Body=png(size=(99, 99)))
    assert worker.process(modified) == "error"
    assert last_audit(sync_engine, modified, "media.scan_failed")["reason"] == "integrity_mismatch"
    deleted = png_media(api, mw)
    s3.delete_object(Bucket=QUARANTINE, Key=row(sync_engine, deleted)["quarantine_key"])  # objet de test créé ici
    assert worker.process(deleted) == "error"
    assert last_audit(sync_engine, deleted, "media.scan_failed")["reason"] == "object_missing"
    for mid in (modified, deleted):
        assert row(sync_engine, mid)["state"] == "scan_failed" and row(sync_engine, mid)["clean_key"] is None


def test_unauthorized_worker_identities_are_refused(sync_engine, scanner_dsn, clamd):
    api_dsn = sync_engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with pytest.raises((WorkerConfigError, psycopg.Error)):
        make(api_dsn, clamd).store.connect()  # superutilisateur de l'API : identité de service refusée
    trust = sql(sync_engine, "SELECT count(*) FROM pg_hba_file_rules WHERE auth_method = 'trust'").scalar()
    if trust == 0:  # serveur à mot de passe (CI, Docker) ; une base locale « trust » n'en vérifie aucun
        wrong = make_url(scanner_dsn).set(password="mauvais-mot-de-passe").render_as_string(hide_password=False)
        with pytest.raises(psycopg.Error):
            make(wrong, clamd).store.connect()


# =============================================================================================
# 8–9. Tâches dupliquées et exécution concurrente
# =============================================================================================
def test_duplicate_redis_events_produce_a_single_verdict(api, mw, sync_engine, worker):
    mid = png_media(api, mw)
    q = worker.queue
    for _ in range(3):  # doublons délibérés, hors déduplication
        q.r.xadd(q.stream, {"media_id": mid})
    q.r.xadd(q.stream, {"media_id": "pas-un-identifiant"})
    outcomes = worker.run_once(block_ms=200, reconcile=False)
    assert outcomes.count("clean") == 1 and outcomes.count("skipped") >= 2 and "invalid" in outcomes
    assert len(events(sync_engine, mid)) == 1
    assert worker.run_once(block_ms=100, reconcile=False) == []  # tout est acquitté


def test_two_workers_on_the_same_media(api, mw, sync_engine, scanner_dsn, clamd):
    mid = png_media(api, mw)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def go() -> None:
        w = make(scanner_dsn, clamd)
        barrier.wait()
        results.append(w.process(mid))
        w.reset()

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["clean", "skipped"]
    assert len(events(sync_engine, mid)) == 1 and row(sync_engine, mid)["attempt"] == 1


# =============================================================================================
# 10. Pannes et reprise : rien ne devient accessible sans verdict valide
# =============================================================================================
def test_clamd_unavailable_or_timeout(api, mw, sync_engine, scanner_dsn, clamd):
    mid = png_media(api, mw)
    assert make(scanner_dsn, ("127.0.0.1", 9)).process(mid) == "error"
    assert last_audit(sync_engine, mid, "media.scan_failed")["reason"] == "clamd_unavailable"
    hang = FakeClamd(mode="hang")
    try:
        assert make(scanner_dsn, ("127.0.0.1", hang.port), clamd_timeout_seconds=1).process(mid) in ("timeout", "error")
    finally:
        hang.close()
    assert row(sync_engine, mid)["state"] == "scan_failed" and row(sync_engine, mid)["clean_key"] is None
    assert make(scanner_dsn, clamd).process(mid) == "clean"  # reprise


def test_storage_unavailable(api, mw, sync_engine, scanner_dsn, clamd):
    mid = png_media(api, mw)
    assert make(scanner_dsn, clamd, s3_endpoint="http://127.0.0.1:9").process(mid) == "error"
    assert last_audit(sync_engine, mid, "media.scan_failed")["reason"] == "storage_unavailable"
    assert make(scanner_dsn, clamd).process(mid) == "clean"


def test_redis_and_postgres_unavailable_change_nothing(api, mw, sync_engine, scanner_dsn, clamd):
    mid = png_media(api, mw)
    import redis

    with pytest.raises(redis.ConnectionError):
        make(scanner_dsn, clamd, redis_url="redis://127.0.0.1:9/0").run_once(block_ms=10)
    down = make_url(scanner_dsn).set(port=9).render_as_string(hide_password=False)
    with pytest.raises(psycopg.Error):
        make(down, clamd).process(mid)
    assert row(sync_engine, mid)["state"] == "quarantined" and events(sync_engine, mid) == []


@pytest.mark.parametrize("step", ["downloaded", "scanned", "copied", "before_commit"])
def test_worker_killed_mid_scan_is_recovered_safely(api, mw, sync_engine, scanner_dsn, clamd, step):
    mid = png_media(api, mw)
    keys_before = clean_keys()

    def crash(at: str) -> None:
        if at == step:
            raise SystemExit(f"arrêt simulé ({at})")

    with pytest.raises(SystemExit):
        MediaScanWorker(worker_settings(scanner_dsn, clamd), hook=crash).process(mid)
    assert row(sync_engine, mid)["state"] == "scanning"  # rien n'est validé
    reconciler = make(scanner_dsn, clamd, media_scan_lease_seconds=1)
    time.sleep(1.2)
    rep: Report = reconciler.reconcile()
    assert rep.lease_expired >= 1
    assert row(sync_engine, mid)["state"] == "scan_failed"
    assert events(sync_engine, mid)[-1][1] == "timeout"
    if step in ("copied", "before_commit"):
        assert rep.orphans_clean >= 1 and len(clean_keys() - keys_before) == 1  # signalée, jamais supprimée
    assert make(scanner_dsn, clamd).process(mid) == "clean"
    assert row(sync_engine, mid)["attempt"] == 2


def test_commit_failure_removes_only_its_own_clean_copy(api, mw, sync_engine, worker, monkeypatch):
    mid = png_media(api, mw)
    keys_before = clean_keys()

    def db_down(*_: Any, **__: Any) -> None:
        raise psycopg.OperationalError("connexion perdue (simulée)")

    monkeypatch.setattr(worker.store, "record", db_down)
    with pytest.raises(psycopg.OperationalError):
        worker.process(mid)
    assert clean_keys() == keys_before and row(sync_engine, mid)["state"] == "scanning"


def test_lost_lease_verdict_is_rejected(api, mw, sync_engine, scanner_dsn, clamd):
    """Un worker lent termine APRÈS la clôture de sa tentative : son verdict est refusé, sa copie retirée."""
    mid = png_media(api, mw)
    keys_before = clean_keys()
    slow = make(scanner_dsn, clamd)
    closer = make(scanner_dsn, clamd, media_scan_lease_seconds=1)

    def expire(at: str) -> None:
        if at == "before_commit":
            time.sleep(1.2)
            closer.reconcile(enqueue=False)

    slow.hook = expire
    with pytest.raises((RuntimeError, psycopg.Error)):  # tentative perdue : verdict refusé par la base
        slow.process(mid)
    assert row(sync_engine, mid)["state"] == "scan_failed" and clean_keys() == keys_before


# =============================================================================================
# Réconciliation : tâches perdues, objets orphelins, aucune suppression
# =============================================================================================
def test_reconciliation_requeues_lost_tasks_and_reports_orphans(api, mw, sync_engine, worker):
    mid = png_media(api, mw)  # jamais mis en file : tâche « perdue »
    orphan = f"{uuid.uuid4()}/{uuid.uuid4()}"
    s3_client().put_object(Bucket=QUARANTINE, Key=orphan, Body=b"orphelin fictif")
    outcomes = worker.run_once(block_ms=300)
    assert "clean" in outcomes and row(sync_engine, mid)["state"] == "clean"
    rep = worker.reconcile()
    assert rep.orphans_quarantine >= 1
    assert s3_client().get_object(Bucket=QUARANTINE, Key=orphan)["Body"].read() == b"orphelin fictif"  # conservé
    report = sql(
        sync_engine, "SELECT metadata FROM audit_logs WHERE action = 'media.reconcile_report' ORDER BY id DESC LIMIT 1"
    ).scalar()
    assert report["orphans_quarantine"] >= 1


# =============================================================================================
# 11–14. Isolation, quarantaine privée, aucun lien public, aucune diffusion
# =============================================================================================
def test_isolation_privacy_and_no_distribution(api, mw, sync_engine, worker):
    mid = png_media(api, mw, who=mw.org_instr, organization_id=mw.org_a)
    assert worker.process(mid) == "clean"
    assert api.get(f"{M}/{mid}", headers=mw.other_instr.h).status_code == 404  # autre établissement
    assert api.get(f"{M}/{mid}", headers=mw.student.h).status_code in (403, 404)  # étudiant
    paths = {p for p in api.app.openapi()["paths"] if p.startswith(M)}  # type: ignore[attr-defined]
    # 2.2.3-d : seule la révocation s'ajoute ; l'API d'édition ne sert toujours aucun octet.
    assert paths == {M, f"{M}/{{media_id}}", f"{M}/{{media_id}}/content", f"{M}/{{media_id}}/revoke"}
    assert "get" not in api.app.openapi()["paths"][f"{M}/{{media_id}}/content"]  # type: ignore[attr-defined]
    r = row(sync_engine, mid)
    if REAL_S3:
        import urllib.error
        import urllib.request

        for bucket, key in ((CLEAN, r["clean_key"]), (QUARANTINE, r["quarantine_key"])):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"{S3}/{bucket}/{key}", timeout=5)  # noqa: S310 - test de non-exposition
            assert exc.value.code == 403  # aucun accès anonyme, aucun lien public

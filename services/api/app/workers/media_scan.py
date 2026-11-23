"""Worker d'analyse antivirus des médias (étape 2.2.3-c, ADR-0011, S5–S9, S14).

Usage : python -m app.workers.media_scan            (boucle continue, conteneur `media-worker`)
        python -m app.workers.media_scan --once     (une passe : réconciliation + file, puis arrêt)

Principes :
* PostgreSQL est la source de vérité. Redis ne fait que DISTRIBUER les tâches : un message perdu,
  rejoué ou dupliqué ne change jamais un verdict (prise en charge par UPDATE conditionnel sous verrou).
* Connexion PostgreSQL DÉDIÉE `nl_media_scanner` (privilèges minimaux) + acteur = compte de service :
  la base refuse tout verdict venant d'une autre connexion ou d'une autre identité (migration 0008).
* « clean » exige : octets relus identiques (taille + SHA-256), réponse exacte « stream: OK » de clamd,
  signatures datées et fraîches (≤ 48 h), copie saine écrite et vérifiée AVANT l'enregistrement.
  Toute autre issue (panne, délai, réponse inattendue, signatures périmées) = « scan_failed » ou
  tentative laissée « scanning » puis close par la réconciliation : jamais « clean ».
* Le worker ne supprime jamais un objet, sauf la copie saine qu'il vient d'écrire si l'enregistrement
  du verdict échoue. Les orphelins sont SIGNALÉS (audit), jamais effacés automatiquement.
* Audit sans contenu sensible : ni nom de fichier, ni texte, ni clé de stockage, ni octets.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import signal
import socket
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import redis as redis_lib
from psycopg.types.json import Jsonb

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domains.media.storage import MediaStorage, ObjectMissing, StorageUnavailable, random_key
from app.workers.clamd import ClamdClient, ClamdError, ClamdTimeout, ClamdUnavailable, EngineInfo

log = logging.getLogger("nurselearn.media.scan")

SERVICE_ACCOUNT = "00000000-0000-4000-a000-00000000c1a5"  # identifiant fixe (migration 0008)
GROUP = "media-scanners"
# Fichier de vie (contrôle de santé du conteneur) : aucun contenu, seule sa date compte.
HEARTBEAT = Path(os.environ.get("NL_WORKER_HEARTBEAT", "/tmp/nl-media-worker.alive"))  # noqa: S108
STATE_FOR = {"clean": "clean", "infected": "infected", "error": "scan_failed", "timeout": "scan_failed"}


class WorkerConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    media_id: str
    organization_id: str | None
    byte_size: int
    sha256: str
    quarantine_key: str
    attempt: int


@dataclass
class Verdict:
    result: str  # clean | infected | error | timeout
    reason: str
    sha256: str
    engine: EngineInfo | None = None
    threat_name: str | None = None


@dataclass
class Report:
    lease_expired: int = 0
    enqueued: int = 0
    orphans_quarantine: int = 0
    orphans_clean: int = 0
    missing_clean: int = 0
    attempts_exhausted: int = 0
    expired_reservations: int = 0
    storage_unavailable: bool = False
    samples: list[str] = field(default_factory=list)

    def anything(self) -> bool:
        return any(
            (
                self.lease_expired,
                self.orphans_quarantine,
                self.orphans_clean,
                self.missing_clean,
                self.attempts_exhausted,
                self.storage_unavailable,
            )
        )


def pg_dsn(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


# ============================================================================================ base
class Store:
    """Accès PostgreSQL de la connexion de service. Chaque opération = une transaction courte."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn: psycopg.Connection[Any] | None = None

    def connect(self) -> None:
        if self.conn is not None and not self.conn.closed:
            return
        self.conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
        with self.conn.cursor() as cur:
            cur.execute("SELECT session_user, nl_media_scanner_identity()::text")
            row = cur.fetchone()
        if row is None or row[0] != "nl_media_scanner" or row[1] != SERVICE_ACCOUNT:
            self.close()
            raise WorkerConfigError("connexion PostgreSQL : identité de service nl_media_scanner requise")

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    @contextmanager
    def tx(self) -> Iterator[psycopg.Cursor[Any]]:
        self.connect()
        if self.conn is None:
            raise WorkerConfigError("connexion PostgreSQL absente")
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT set_config('nl.actor_id', %s, true)", (SERVICE_ACCOUNT,))
            yield cur

    def audit(self, cur: psycopg.Cursor[Any], action: str, media_id: str | None, meta: dict[str, Any]) -> None:
        cur.execute("SELECT nl_media_scanner_audit(%s, %s, %s)", (action, media_id, Jsonb(meta)))

    def claim(self, media_id: str, max_attempts: int, retry_base: int) -> Claim | None:
        """Prise en charge atomique : une seule tentative à la fois, quel que soit le nombre de workers."""
        with self.tx() as cur:
            cur.execute(
                """
                UPDATE media_assets SET state = 'scanning', scan_attempt = scan_attempt + 1
                 WHERE id = %(id)s AND deleted_at IS NULL AND purged_at IS NULL AND scan_attempt < %(max)s
                   AND (state = 'quarantined' OR (state = 'scan_failed' AND updated_at <= now()
                        - make_interval(secs => least(%(base)s * power(2, greatest(scan_attempt - 1, 0)), 3600))))
                RETURNING id::text, organization_id::text, byte_size, sha256, quarantine_key, scan_attempt
                """,
                {"id": media_id, "max": max_attempts, "base": retry_base},
            )
            row = cur.fetchone()
            if row is None:
                return None
            claim = Claim(row[0], row[1], int(row[2]), row[3], row[4], int(row[5]))
            self.audit(cur, "media.scan_started", claim.media_id, {"attempt": claim.attempt})
            return claim

    def record(self, claim: Claim, v: Verdict, clean_key: str | None, duration_ms: int) -> None:
        """Verdict + transition + audit dans UNE transaction ; la base revérifie tout (0007, 0008)."""
        eng = v.engine
        with self.tx() as cur:
            cur.execute(
                """
                INSERT INTO media_scan_events (media_id, attempt, result, engine, engine_version, signature_version,
                       signatures_published_at, threat_name, sha256, duration_ms, scanned_by)
                VALUES (%s, %s, %s, 'clamav', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    claim.media_id,
                    claim.attempt,
                    v.result,
                    eng.engine_version if eng else None,
                    eng.signature_version if eng else None,
                    eng.signatures_published_at if eng else None,
                    v.threat_name,
                    v.sha256,
                    duration_ms,
                    SERVICE_ACCOUNT,
                ),
            )
            state = STATE_FOR[v.result]
            if state == "clean":
                cur.execute(
                    "UPDATE media_assets SET state = 'clean', clean_key = %s WHERE id = %s AND state = 'scanning' "
                    "AND scan_attempt = %s RETURNING id",
                    (clean_key, claim.media_id, claim.attempt),
                )
            else:
                cur.execute(
                    "UPDATE media_assets SET state = %s WHERE id = %s AND state = 'scanning' AND scan_attempt = %s "
                    "RETURNING id",
                    (state, claim.media_id, claim.attempt),
                )
            if cur.fetchone() is None:
                raise RuntimeError("tentative d'analyse perdue (bail expiré ou média modifié)")
            meta: dict[str, Any] = {
                "attempt": claim.attempt,
                "result": v.result,
                "reason": v.reason,
                "duration_ms": duration_ms,
                "byte_size": claim.byte_size,
            }
            if eng:
                meta |= {"engine_version": eng.engine_version, "signature_version": eng.signature_version}
            if v.threat_name:
                meta["threat_name"] = v.threat_name
            action = {"clean": "media.scan_clean", "infected": "media.scan_infected"}.get(v.result, "media.scan_failed")
            self.audit(cur, action, claim.media_id, meta)

    def close_expired_leases(self, lease_seconds: int) -> int:
        """Tentatives restées « scanning » au-delà du bail (worker arrêté, panne) : verdict « timeout »."""
        closed = 0
        with self.tx() as cur:
            cur.execute(
                "SELECT id::text, scan_attempt, sha256 FROM media_assets WHERE state = 'scanning' "
                "AND updated_at < now() - make_interval(secs => %s) "
                "ORDER BY updated_at LIMIT 200 FOR UPDATE SKIP LOCKED",
                (lease_seconds,),
            )
            for mid, attempt, sha in cur.fetchall():
                cur.execute(
                    "INSERT INTO media_scan_events (media_id, attempt, result, engine, sha256, scanned_by) "
                    "VALUES (%s, %s, 'timeout', 'clamav', %s, %s)",
                    (mid, attempt, sha, SERVICE_ACCOUNT),
                )
                cur.execute(
                    "UPDATE media_assets SET state = 'scan_failed' WHERE id = %s AND state = 'scanning'", (mid,)
                )
                self.audit(cur, "media.reconcile_lease_expired", mid, {"attempt": attempt})
                closed += 1
        return closed

    def pending(self, max_attempts: int, retry_base: int, limit: int = 500) -> list[str]:
        with self.tx() as cur:
            cur.execute(
                """
                SELECT id::text FROM media_assets
                 WHERE deleted_at IS NULL AND purged_at IS NULL AND scan_attempt < %(max)s
                   AND (state = 'quarantined' OR (state = 'scan_failed' AND updated_at <= now()
                        - make_interval(secs => least(%(base)s * power(2, greatest(scan_attempt - 1, 0)), 3600))))
                 ORDER BY updated_at LIMIT %(limit)s
                """,
                {"max": max_attempts, "base": retry_base, "limit": limit},
            )
            return [r[0] for r in cur.fetchall()]

    def stuck_counts(self, max_attempts: int) -> tuple[int, int]:
        with self.tx() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE state = 'scan_failed' AND scan_attempt >= %s AND deleted_at IS NULL), "
                "count(*) FILTER (WHERE state = 'pending_upload' AND deleted_at IS NULL "
                "AND reservation_expires_at <= now()) "
                "FROM media_assets",
                (max_attempts,),
            )
            row = cur.fetchone()
            return (int(row[0]), int(row[1])) if row else (0, 0)

    def known_keys(self) -> tuple[set[str], set[str], set[str]]:
        """(clés de quarantaine connues, clés saines connues, clés saines des médias « clean »)."""
        with self.tx() as cur:
            cur.execute(
                "SELECT quarantine_key, clean_key, state FROM media_assets "
                "WHERE quarantine_key IS NOT NULL OR clean_key IS NOT NULL"
            )
            rows = cur.fetchall()
        q = {r[0] for r in rows if r[0]}
        c = {r[1] for r in rows if r[1]}
        live = {r[1] for r in rows if r[1] and r[2] == "clean"}
        return q, c, live

    def report(self, rep: Report) -> None:
        meta = {k: v for k, v in rep.__dict__.items() if k != "samples"} | {"samples": rep.samples[:5]}
        with self.tx() as cur:
            self.audit(cur, "media.reconcile_report", None, meta)


# ============================================================================================ file
class Queue:
    """Distribution par Redis Streams (groupe de consommateurs). Jamais une source de vérité."""

    def __init__(self, client: Any, stream: str, consumer: str) -> None:
        self.r, self.stream, self.consumer = client, stream, consumer

    def ensure_group(self) -> None:
        try:
            self.r.xgroup_create(self.stream, GROUP, id="0", mkstream=True)
        except redis_lib.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(self, media_id: str, dedupe_seconds: int) -> bool:
        if self.r.set(f"{self.stream}:queued:{media_id}", "1", nx=True, ex=max(dedupe_seconds, 1)):
            self.r.xadd(self.stream, {"media_id": media_id}, maxlen=10_000, approximate=True)
            return True
        return False

    def read(self, block_ms: int) -> list[tuple[str, str]]:
        resp = self.r.xreadgroup(GROUP, self.consumer, {self.stream: ">"}, count=1, block=block_ms)
        return [(mid, fields.get("media_id", "")) for _, msgs in resp or [] for mid, fields in msgs]

    def reclaim(self, idle_ms: int) -> list[tuple[str, str]]:
        resp = self.r.xautoclaim(self.stream, GROUP, self.consumer, min_idle_time=idle_ms, start_id="0-0", count=20)
        msgs = resp[1] if resp else []
        return [(mid, (fields or {}).get("media_id", "")) for mid, fields in msgs]

    def ack(self, msg_id: str, media_id: str) -> None:
        self.r.xack(self.stream, GROUP, msg_id)
        self.r.delete(f"{self.stream}:queued:{media_id}")

    def leader(self, ttl_ms: int) -> bool:
        return bool(self.r.set(f"{self.stream}:reconcile-lock", self.consumer, nx=True, px=ttl_ms))


# ============================================================================================ worker
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class MediaScanWorker:
    def __init__(self, settings: Settings, hook: Callable[[str], None] | None = None) -> None:
        if not settings.media_scanner_database_url:
            raise WorkerConfigError("MEDIA_SCANNER_DATABASE_URL absent")
        if not settings.clamd_host:
            raise WorkerConfigError("CLAMD_HOST absent")
        self.s = settings
        self.store = Store(pg_dsn(settings.media_scanner_database_url))
        self.storage = MediaStorage(settings)
        self.clamd = ClamdClient(settings.clamd_host, settings.clamd_port, settings.clamd_timeout_seconds)
        self.hook = hook or (lambda step: None)
        self._queue: Queue | None = None

    # ------------------------------------------------------------------ file Redis
    @property
    def queue(self) -> Queue:
        if self._queue is None:
            if not self.s.redis_url:
                raise WorkerConfigError("REDIS_URL absent")
            client = redis_lib.Redis.from_url(
                self.s.redis_url, decode_responses=True, socket_timeout=10, socket_connect_timeout=5
            )
            self._queue = Queue(client, self.s.media_scan_stream, f"{socket.gethostname()}-{os.getpid()}")
            self._queue.ensure_group()
        return self._queue

    def reset(self) -> None:
        self.store.close()
        self._queue = None

    # ------------------------------------------------------------------ analyse d'un média
    def _fresh(self, eng: EngineInfo) -> bool:
        if eng.signatures_published_at is None:
            return False
        now = datetime.now(UTC)
        age = now - eng.signatures_published_at
        return timedelta(minutes=-5) <= age <= timedelta(hours=self.s.media_signature_max_age_hours)

    def _analyse(self, claim: Claim, path: Path) -> Verdict:
        try:
            size = self.storage.download_quarantine_sync(claim.quarantine_key, path, claim.byte_size)
        except ObjectMissing:
            return Verdict("error", "object_missing", claim.sha256)
        except StorageUnavailable:
            return Verdict("error", "storage_unavailable", claim.sha256)
        seen = _sha256(path)
        if size != claim.byte_size or seen != claim.sha256:
            return Verdict("error", "integrity_mismatch", seen)  # objet modifié : jamais analysé ni validé
        self.hook("downloaded")
        try:
            eng = self.clamd.version()
        except ClamdUnavailable:
            return Verdict("error", "clamd_unavailable", seen)
        except (ClamdError, ClamdTimeout):
            return Verdict("error", "clamd_error", seen)
        try:
            res = self.clamd.scan_file(path)
        except ClamdTimeout:
            return Verdict("timeout", "scan_timeout", seen, eng)
        except ClamdUnavailable:
            return Verdict("error", "clamd_unavailable", seen, eng)
        except ClamdError:
            return Verdict("error", "clamd_error", seen, eng)
        self.hook("scanned")
        if res.infected:
            return Verdict("infected", "threat_found", seen, eng, res.threat_name)
        if not self._fresh(eng):
            return Verdict("error", "signatures_stale", seen, eng)
        return Verdict("clean", "no_threat", seen, eng)

    def process(self, media_id: str) -> str:
        """Traite un média ; renvoie l'issue (« skipped » si déjà pris, terminé ou non éligible)."""
        try:
            uuid.UUID(media_id)
        except ValueError:
            return "invalid"
        claim = self.store.claim(media_id, self.s.media_scan_max_attempts, self.s.media_scan_retry_base_seconds)
        if claim is None:
            return "skipped"
        self.hook("claimed")
        started = time.monotonic()
        tmp = Path(tempfile.mkdtemp(prefix="nl-scan-", dir=self.s.media_tmp_dir))
        os.chmod(tmp, 0o700)
        try:
            path = tmp / "object.bin"
            verdict = self._analyse(claim, path)
            clean_key: str | None = None
            if verdict.result == "clean":
                clean_key = random_key()
                try:
                    self.storage.put_clean_sync(clean_key, path, claim.sha256)
                except StorageUnavailable:
                    self.storage.delete_own_clean_copy_sync(clean_key)
                    clean_key, verdict = None, Verdict("error", "clean_copy_failed", verdict.sha256, verdict.engine)
                else:
                    self.hook("copied")
            self.hook("before_commit")
            duration = int((time.monotonic() - started) * 1000)
            try:
                self.store.record(claim, verdict, clean_key, duration)
            except Exception:
                if clean_key:
                    self.storage.delete_own_clean_copy_sync(clean_key)
                raise
            log.info(
                "analyse_terminee",
                extra={
                    "media_id": claim.media_id,
                    "attempt": claim.attempt,
                    "result": verdict.result,
                    "reason": verdict.reason,
                },
            )
            return verdict.result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------ réconciliation
    def reconcile(self, enqueue: bool = True) -> Report:
        rep = Report()
        rep.lease_expired = self.store.close_expired_leases(self.s.media_scan_lease_seconds)
        if enqueue:
            for mid in self.store.pending(self.s.media_scan_max_attempts, self.s.media_scan_retry_base_seconds):
                rep.enqueued += int(self.queue.enqueue(mid, self.s.media_scan_lease_seconds))
        rep.attempts_exhausted, rep.expired_reservations = self.store.stuck_counts(self.s.media_scan_max_attempts)
        try:
            in_q = self.storage.list_keys_sync(self.storage.quarantine)
            in_c = self.storage.list_keys_sync(self.storage.clean)
        except StorageUnavailable:
            rep.storage_unavailable = True
        else:
            q, c, live = self.store.known_keys()
            orphans_q, orphans_c, missing = in_q - q, in_c - c, live - in_c
            rep.orphans_quarantine, rep.orphans_clean, rep.missing_clean = len(orphans_q), len(orphans_c), len(missing)
            rep.samples = sorted(orphans_q | orphans_c)[:5]
        if rep.anything():
            self.store.report(rep)
        return rep

    # ------------------------------------------------------------------ boucle
    def handle(self, msg_id: str, media_id: str) -> str:
        outcome = self.process(media_id)
        self.queue.ack(msg_id, media_id)  # après l'enregistrement : un arrêt avant l'acquittement = rejeu sûr
        return outcome

    def run_once(self, block_ms: int = 1000, reconcile: bool = True) -> list[str]:
        """Une passe : réconciliation (sous verrou), messages en attente repris, puis la file."""
        outcomes: list[str] = []
        if reconcile and self.queue.leader(self.s.media_scan_reconcile_seconds * 1000):
            self.reconcile()
        for msg_id, mid in self.queue.reclaim(self.s.media_scan_lease_seconds * 1000):
            outcomes.append(self.handle(msg_id, mid))
        while batch := self.queue.read(block_ms):
            for msg_id, mid in batch:
                outcomes.append(self.handle(msg_id, mid))
            block_ms = 1
        return outcomes


def run_forever(settings: Settings) -> int:
    worker = MediaScanWorker(settings)
    stop = {"flag": False}

    def _stop(*_: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    delay = 1.0
    next_reconcile = 0.0
    while not stop["flag"]:
        try:
            worker.store.connect()
            if time.monotonic() >= next_reconcile:
                if worker.queue.leader(settings.media_scan_reconcile_seconds * 1000):
                    worker.reconcile()
                next_reconcile = time.monotonic() + settings.media_scan_reconcile_seconds
            for msg_id, mid in worker.queue.reclaim(settings.media_scan_lease_seconds * 1000):
                worker.handle(msg_id, mid)
            for msg_id, mid in worker.queue.read(5000):
                worker.handle(msg_id, mid)
            HEARTBEAT.touch()
            delay = 1.0
        except WorkerConfigError:
            log.error("configuration_invalide", exc_info=True)
            return 2
        except Exception as exc:  # Redis, PostgreSQL, RustFS, clamd : rien n'est validé, on réessaie
            log.warning("worker_en_attente", extra={"error": type(exc).__name__, "retry_s": delay})
            worker.reset()
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker d'analyse antivirus des médias")
    parser.add_argument("--once", action="store_true", help="une seule passe puis arrêt")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        if args.once:
            print(MediaScanWorker(settings).run_once())
            return 0
        return run_forever(settings)
    except WorkerConfigError as exc:
        log.error("configuration_invalide", extra={"error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())

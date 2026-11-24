"""Diffusion sécurisée des médias sains (étape 2.2.3-d, D18, D24, S1–S3).

Service SÉPARÉ (`media-delivery`, `app.main:create_delivery_app`) : il détient la seule identité S3 de
LECTURE (`nl-media-reader`, compartiment sain uniquement) et aucune autre ; l'API d'édition (identité de
téléversement) ne sert jamais d'octets.

Aucun jeton de diffusion autonome, aucune URL S3 publique ni présignée, aucune redirection : chaque
requête — y compris chaque requête `Range` — présente la session (Bearer) et est autorisée à neuf :

1. session valide, utilisateur actif (sinon 401) ;
2. limites de débit par utilisateur, établissement et point d'accès (sinon 429 ; Redis absent = 503) ;
3. droit d'usage : `learning:participate` (apprenant) ou droit éditorial dans la portée (aperçu)
   (sinon 403 pour l'apprenant, 404 hors portée pour l'aperçu : existence non révélée) ;
4. média `clean`, ni supprimé ni purgé, clé saine posée, DERNIER verdict ClamAV traçable « clean »
   de la tentative courante et des mêmes octets (sinon 404) ;
5. apprenant : édition EXPOSÉE par le catalogue (règle 2.2.2-c), version de leçon citée par cette
   édition, média rattaché à cette version (ou sous-titres d'une vidéo rattachée), média public
   (sinon 404) ;
6. objet sain présent et cohérent avec la base (taille, SHA-256 en métadonnée) (sinon 503) ;
7. plage HTTP valide (sinon 416) ;
8. pendant le flux : revérification périodique (session, état, époque `access_epoch`) ; un échec
   coupe la connexion. Les octets déjà transmis ne peuvent pas être repris.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.audit import audit
from app.core.config import Settings
from app.core.errors import problem_response
from app.domains.catalog.editions import EXPOSABLE_SQL
from app.domains.editorial.scope import can_read
from app.domains.identity.deps import _bearer, client_ip, user_agent
from app.domains.identity.sessions import AuthContext, authenticate
from app.domains.media.storage import _client

router = APIRouter(prefix="/api/v1/media", tags=["diffusion des médias"])
log = logging.getLogger("nurselearn.media.delivery")
BASE = "https://nurselearn.ai/problems/"
LEARN = "learning:participate"

# Type, disposition et extension par type MIME (la valeur vient de la BASE, jamais du stockage).
FORMATS = {
    "image/jpeg": ("inline", "jpg"),
    "image/png": ("inline", "png"),
    "image/webp": ("inline", "webp"),
    "video/mp4": ("inline", "mp4"),
    "text/vtt": ("inline", "vtt"),
    "application/pdf": ("attachment", "pdf"),
}
RANGE_KINDS = {"video"}  # seule la vidéo accepte les plages ; ailleurs l'en-tête Range est ignoré (RFC 9110)
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

# Dernier verdict de la tentative courante, sur les mêmes octets, traçable (moteur et signatures).
VERDICT_OK = """EXISTS (
  SELECT 1 FROM (SELECT e.result, e.attempt, e.sha256, e.engine_version, e.signature_version
                   FROM media_scan_events e WHERE e.media_id = m.id
                  ORDER BY e.scanned_at DESC, e.id DESC LIMIT 1) v
   WHERE v.result = 'clean' AND v.attempt = m.scan_attempt AND v.sha256 = m.sha256
     AND v.engine_version IS NOT NULL AND v.signature_version IS NOT NULL)"""

MEDIA_SQL = """
SELECT m.id, m.organization_id, m.kind, m.mime_type, m.byte_size, m.sha256, m.clean_key, m.access_epoch,
       m.display_name, m.captions_for,
       (m.state = 'clean' AND m.deleted_at IS NULL AND m.purged_at IS NULL AND m.clean_key IS NOT NULL
        AND m.byte_size IS NOT NULL AND m.sha256 IS NOT NULL) AS servable,
       @VERDICT@ AS verdict_ok
  FROM media_assets m WHERE m.id = :id
""".replace("@VERDICT@", VERDICT_OK)  # fragment statique du module

# Apprenant : édition exposée, version de leçon citée par l'édition, média (ou vidéo sous-titrée) rattaché.
LEARNER_SQL = """
WITH exposable AS (@EXPOSABLE@)
SELECT EXISTS (SELECT 1 FROM exposable e WHERE e.release_id = :release)
   AND EXISTS (SELECT 1 FROM nl_release_lessons(:release) x WHERE x.lesson_version_id = :lv)
   AND (EXISTS (SELECT 1 FROM lesson_version_media a WHERE a.lesson_version_id = :lv AND a.media_id = :id)
        OR (CAST(:captions_for AS uuid) IS NOT NULL
            AND EXISTS (SELECT 1 FROM lesson_version_media a
                         WHERE a.lesson_version_id = :lv AND a.media_id = CAST(:captions_for AS uuid))))
""".replace("@EXPOSABLE@", EXPOSABLE_SQL)  # règle d'exposition du catalogue (2.2.2-c), statique


class DeliveryError(Exception):
    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers or {}


def _err(status: int, code: str, detail: str, **headers: str) -> DeliveryError:
    return DeliveryError(status, code, detail, headers)


NOT_FOUND = ("not-found", "Média indisponible")


@dataclass
class Grant:
    """Résultat d'une autorisation : ce qui doit rester identique pendant tout le flux."""

    media_id: uuid.UUID
    organization_id: uuid.UUID | None
    kind: str
    mime_type: str
    byte_size: int
    sha256: str
    clean_key: str
    access_epoch: int
    display_name: str


@dataclass
class Target:
    route: str  # "content" (apprenant) | "preview" (aperçu éditorial)
    media_id: uuid.UUID
    release_id: uuid.UUID | None = None
    lesson_version_id: uuid.UUID | None = None


# =============================================================================================
# Autorisation (relue à chaque requête et pendant les flux longs)
# =============================================================================================
def _learner(ctx: AuthContext) -> bool:
    return LEARN in ctx.global_permissions or any(LEARN in p for p in ctx.org_permissions.values())


async def authorize(db: AsyncSession, ctx: AuthContext, target: Target) -> Grant:
    if target.route == "content" and not _learner(ctx):
        raise _err(403, "forbidden", "Accès aux contenus de formation non autorisé pour ce compte")
    row = (await db.execute(text(MEDIA_SQL), {"id": target.media_id})).mappings().first()
    if row is None:
        raise _err(404, *NOT_FOUND)
    if target.route == "preview":
        # Aperçu éditorial : droit de lecture éditoriale dans la portée du média (établissement, ou
        # plateforme pour un média public) ; hors portée, l'existence n'est pas révélée.
        if not can_read(ctx, row["organization_id"]):
            raise _err(404, *NOT_FOUND)
    else:
        if row["organization_id"] is not None:  # le catalogue n'expose que des contenus publics
            raise _err(404, *NOT_FOUND)
        linked = (
            await db.execute(
                text(LEARNER_SQL),
                {
                    "release": target.release_id,
                    "lv": target.lesson_version_id,
                    "id": target.media_id,
                    "captions_for": str(row["captions_for"]) if row["kind"] == "captions" else None,
                },
            )
        ).scalar()
        if not linked:
            raise _err(404, *NOT_FOUND)
    if not (row["servable"] and row["verdict_ok"]) or row["mime_type"] not in FORMATS:
        raise _err(404, *NOT_FOUND)
    return Grant(
        media_id=row["id"],
        organization_id=row["organization_id"],
        kind=row["kind"],
        mime_type=row["mime_type"],
        byte_size=int(row["byte_size"]),
        sha256=str(row["sha256"]),
        clean_key=str(row["clean_key"]),
        access_epoch=int(row["access_epoch"]),
        display_name=str(row["display_name"]),
    )


async def _session(request: Request, db: AsyncSession, token: str | None) -> AuthContext:
    if token is None:
        raise _err(401, "unauthenticated", "Authentification requise")
    ctx = await authenticate(
        db, request.app.state.settings, token, ip=client_ip(request), user_agent=user_agent(request)
    )
    if ctx is None:
        raise _err(401, "unauthenticated", "Authentification requise")
    return ctx


# =============================================================================================
# Limitation de débit (Redis, fermé par défaut)
# =============================================================================================
async def _limit(request: Request, checks: list[tuple[str, int, int]]) -> None:
    """checks : (clé, incrément, limite). Toute limite dépassée → 429 ; Redis absent → 503."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise _err(503, "media_unavailable", "Service de diffusion indisponible")
    try:
        async with redis.pipeline(transaction=True) as pipe:
            for key, amount, _ in checks:
                pipe.incrby(key, amount)
                pipe.expire(key, 70)
            results = await pipe.execute()
    except Exception:
        raise _err(503, "media_unavailable", "Service de diffusion indisponible") from None
    for (_, _, limit), count in zip(checks, results[0::2], strict=True):
        if limit > 0 and int(count) > limit:
            raise _err(
                429,
                "rate_limited",
                "Trop de requêtes de diffusion : réessayez plus tard",
                **{"Retry-After": str(60 - int(time.time()) % 60)},
            )


def _minute() -> int:
    return int(time.time() // 60)


def _ns(s: Settings) -> str:
    """Espace de noms des compteurs : commun à toutes les instances (RATE_LIMIT_SALT partagé)."""
    import hashlib

    return hashlib.sha256(s.effective_rate_limit_salt.encode()).hexdigest()[:8]


def _org_keys(ctx: AuthContext) -> list[str]:
    orgs = sorted({str(o) for _, o in ctx.roles if o is not None})
    return orgs or ["individuel"]


async def _request_limits(request: Request, s: Settings, ctx: AuthContext, route: str) -> None:
    per_user = (
        s.media_delivery_content_per_minute_user if route == "content" else s.media_delivery_preview_per_minute_user
    )
    m = _minute()
    checks = [(f"nl:md:{_ns(s)}:rq:{route}:u:{ctx.user_id}:{m}", 1, per_user)]
    checks += [(f"nl:md:{_ns(s)}:rq:o:{org}:{m}", 1, s.media_delivery_per_minute_org) for org in _org_keys(ctx)]
    await _limit(request, checks)


async def _transfer_limits(
    request: Request, s: Settings, ctx: AuthContext, grant: Grant, length: int, ranged: bool
) -> None:
    m = _minute()
    checks = [(f"nl:md:{_ns(s)}:by:u:{ctx.user_id}:{m}", length, s.media_delivery_bytes_per_minute_user)]
    if ranged:
        checks.append(
            (f"nl:md:{_ns(s)}:rg:u:{ctx.user_id}:{grant.media_id}:{m}", 1, s.media_delivery_range_per_minute_media)
        )
    await _limit(request, checks)


async def _acquire_stream(request: Request, s: Settings, ctx: AuthContext) -> str | None:
    """Flux simultanés par utilisateur (compteur Redis à expiration de sécurité)."""
    if s.media_delivery_concurrent_streams_user <= 0:
        return None
    key = f"nl:md:{_ns(s)}:cc:u:{ctx.user_id}"
    redis = request.app.state.redis
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, 3600)
            count, _ = await pipe.execute()
        if int(count) > s.media_delivery_concurrent_streams_user:
            await redis.decr(key)
            raise _err(429, "too_many_streams", "Trop de lectures simultanées", **{"Retry-After": "5"})
    except DeliveryError:
        raise
    except Exception:
        raise _err(503, "media_unavailable", "Service de diffusion indisponible") from None
    return key


async def _release_stream(request: Request, key: str | None) -> None:
    if key is None:
        return
    try:
        await request.app.state.redis.decr(key)
    except Exception:  # noqa: BLE001 - l'expiration de sécurité finira par libérer le compteur
        log.warning("compteur_de_flux_non_libere")


# =============================================================================================
# HTTP Range (une seule plage ; aucune plage multiple)
# =============================================================================================
def parse_range(header: str | None, size: int, max_open: int) -> tuple[int, int] | None:
    """Renvoie (début, fin incluse) ou None (réponse complète). Plage invalide → DeliveryError 416."""
    if header is None:
        return None
    unsatisfiable = _err(
        416, "range_not_satisfiable", "Plage demandée invalide", **{"Content-Range": f"bytes */{size}"}
    )
    value = header.strip()
    if "," in value or len(value) > 64:  # plusieurs plages : refusées (aucun contournement des limites)
        raise unsatisfiable
    match = _RANGE.match(value)
    if not match or (match.group(1) == "" and match.group(2) == ""):
        raise unsatisfiable
    first, last = match.group(1), match.group(2)
    if first == "":  # suffixe : les N derniers octets
        n = int(last)
        if n <= 0:
            raise unsatisfiable
        return max(size - n, 0), size - 1
    start = int(first)
    if start >= size:
        raise unsatisfiable
    if last == "":
        return start, min(size - 1, start + max_open - 1)  # plage ouverte : réponse bornée
    end = int(last)
    if end < start:
        raise unsatisfiable
    return start, min(end, size - 1)


# =============================================================================================
# Stockage : identité de LECTURE du compartiment sain uniquement
# =============================================================================================
class CleanReader:
    def __init__(self, s: Settings) -> None:
        if not (s.s3_endpoint and s.s3_access_key and s.s3_secret_key):
            raise _err(503, "media_unavailable", "Service de diffusion indisponible")
        self.client = _client(s.s3_endpoint, s.s3_access_key, s.s3_secret_key, s.s3_region)
        self.bucket = s.media_clean_bucket

    def verify(self, grant: Grant) -> None:
        """Objet présent et cohérent avec la base : taille et empreinte (métadonnée posée par le worker)."""
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=grant.clean_key)
        except Exception as exc:
            log.warning("objet_sain_illisible", extra={"media_id": str(grant.media_id), "error": type(exc).__name__})
            raise _err(503, "media_unavailable", "Média momentanément indisponible") from None
        if (
            int(head.get("ContentLength", -1)) != grant.byte_size
            or head.get("Metadata", {}).get("sha256") != grant.sha256
        ):
            log.error("objet_sain_incoherent", extra={"media_id": str(grant.media_id)})
            raise _err(503, "media_integrity", "Média momentanément indisponible")

    def open(self, grant: Grant, start: int, end: int) -> Any:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=grant.clean_key, Range=f"bytes={start}-{end}")
        except Exception as exc:
            log.warning("objet_sain_illisible", extra={"media_id": str(grant.media_id), "error": type(exc).__name__})
            raise _err(503, "media_unavailable", "Média momentanément indisponible") from None
        if int(obj.get("ContentLength", -1)) != end - start + 1:
            obj["Body"].close()
            raise _err(503, "media_integrity", "Média momentanément indisponible")
        return obj["Body"]


# =============================================================================================
# En-têtes
# =============================================================================================
def safe_filename(name: str, ext: str) -> str:
    """Nom ASCII assaini : lettres, chiffres, « _ », « - » ; jamais de point, de chemin ni de guillemet.
    L'extension vient du type réel (base de données), jamais du nom déclaré."""
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-_")[:80].strip("-_") or "media"
    return f"{base}.{ext}"


def headers_for(grant: Grant) -> dict[str, str]:
    disposition, ext = FORMATS[grant.mime_type]
    content_type = "text/vtt; charset=utf-8" if grant.mime_type == "text/vtt" else grant.mime_type
    return {
        "Content-Type": content_type,
        "Content-Disposition": f'{disposition}; filename="{safe_filename(grant.display_name, ext)}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        # Contenu actif neutralisé : aucun script, aucune ressource, document isolé.
        "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'none'",
        "Cross-Origin-Resource-Policy": "same-site",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Accept-Ranges": "bytes" if grant.kind in RANGE_KINDS else "none",
        "ETag": f'"{grant.sha256[:32]}-{grant.access_epoch}"',
        "Vary": "Authorization, Range",
    }


# =============================================================================================
# Point d'accès commun
# =============================================================================================
def _problem(request: Request, e: DeliveryError) -> Response:
    resp = problem_response(request, e.status, HTTPStatus(e.status).phrase, e.detail, BASE + e.code)
    for k, v in e.headers.items():
        resp.headers[k] = v
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


async def _audit_once(
    request: Request, db: AsyncSession, ctx: AuthContext, grant: Grant, target: Target, ranged: bool
) -> None:
    """`media.access_granted`, au plus une fois par minute, utilisateur et média (jamais de jeton ni d'URL)."""
    key = f"nl:md:{_ns(request.app.state.settings)}:aud:{ctx.user_id}:{grant.media_id}:{_minute()}"
    try:
        first = await request.app.state.redis.set(key, "1", nx=True, ex=70)
    except Exception:  # noqa: BLE001
        first = True
    if not first:
        return
    await audit(
        db,
        "media.access_granted",
        "media_asset",
        grant.media_id,
        actor=ctx.user_id,
        actor_role=ctx.primary_role,
        organization_id=grant.organization_id,
        ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={
            "route": target.route,
            "release_id": str(target.release_id) if target.release_id else None,
            "lesson_version_id": str(target.lesson_version_id) if target.lesson_version_id else None,
            "kind": grant.kind,
            "ranged": ranged,
            "access_epoch": grant.access_epoch,
        },
    )
    await db.commit()


async def deliver(
    request: Request, target: Target, authorization: str | None, range_header: str | None, if_range: str | None
) -> Response:
    s: Settings = request.app.state.settings
    token = _bearer(authorization)
    stream_key: str | None = None
    try:
        async with request.app.state.sessionmaker() as db:
            ctx = await _session(request, db, token)
            await _request_limits(request, s, ctx, target.route)
            grant = await authorize(db, ctx, target)
            rng = None
            if grant.kind in RANGE_KINDS:
                etag = f'"{grant.sha256[:32]}-{grant.access_epoch}"'
                use_range = range_header is not None and (if_range is None or if_range.strip() == etag)
                rng = parse_range(
                    range_header if use_range else None, grant.byte_size, s.media_delivery_max_open_range_bytes
                )
            start, end = rng if rng else (0, grant.byte_size - 1)
            await _transfer_limits(request, s, ctx, grant, end - start + 1, rng is not None)
            reader = CleanReader(s)
            await run_in_threadpool(reader.verify, grant)
            await _audit_once(request, db, ctx, grant, target, rng is not None)
            stream_key = await _acquire_stream(request, s, ctx)
            body = await run_in_threadpool(reader.open, grant, start, end)
    except DeliveryError as e:
        await _release_stream(request, stream_key)
        return _problem(request, e)
    except (SQLAlchemyError, OSError):
        await _release_stream(request, stream_key)
        log.warning("diffusion_base_indisponible")
        return _problem(request, _err(503, "media_unavailable", "Service de diffusion indisponible"))

    headers = headers_for(grant)
    headers["Content-Length"] = str(end - start + 1)
    status = 200
    if rng is not None:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{grant.byte_size}"
    return StreamingResponse(
        _stream(request, s, token or "", target, grant, body, end - start + 1, stream_key),
        status_code=status,
        headers=headers,
        media_type=headers["Content-Type"],
    )


async def _still_allowed(request: Request, token: str, target: Target, grant: Grant) -> bool:
    try:
        async with request.app.state.sessionmaker() as db:
            ctx = await authenticate(db, request.app.state.settings, token)
            if ctx is None:
                return False
            now = await authorize(db, ctx, target)
    except DeliveryError:
        return False
    except Exception:  # noqa: BLE001 - base indisponible : fermeture par défaut
        log.warning("revérification_impossible")
        return False
    return now.access_epoch == grant.access_epoch and now.sha256 == grant.sha256 and now.clean_key == grant.clean_key


async def _stream(
    request: Request,
    s: Settings,
    token: str,
    target: Target,
    grant: Grant,
    body: Any,
    length: int,
    stream_key: str | None,
) -> AsyncIterator[bytes]:
    """Flux à mémoire bornée (un morceau à la fois) ; revérification périodique ; coupure sur échec."""
    chunks: Iterator[bytes] = body.iter_chunks(s.media_delivery_chunk_bytes)
    sent = 0
    checked = time.monotonic()
    try:
        while sent < length:
            if time.monotonic() - checked >= s.media_delivery_recheck_seconds:
                checked = time.monotonic()
                if not await _still_allowed(request, token, target, grant):
                    log.warning("diffusion_interrompue_revocation", extra={"media_id": str(grant.media_id)})
                    return  # connexion close avant Content-Length : le client voit un transfert incomplet
            try:
                chunk = await run_in_threadpool(next, chunks, b"")
            except Exception as exc:  # stockage coupé pendant le flux
                log.warning(
                    "diffusion_interrompue_stockage",
                    extra={"media_id": str(grant.media_id), "error": type(exc).__name__},
                )
                return
            if not chunk:
                log.warning("diffusion_interrompue_stockage", extra={"media_id": str(grant.media_id), "error": "eof"})
                return
            chunk = chunk[: length - sent]
            sent += len(chunk)
            yield chunk
    finally:
        with contextlib.suppress(Exception):
            body.close()
        await _release_stream(request, stream_key)


# =============================================================================================
# Routes
# =============================================================================================
def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


RangeH = Annotated[str | None, Header(alias="Range")]
IfRangeH = Annotated[str | None, Header(alias="If-Range")]
AuthH = Annotated[str | None, Header(alias="Authorization")]


@router.get(
    "/{media_id}/content",
    summary="Média d'une leçon d'une édition publiée (apprenant authentifié ; Range pour la vidéo)",
    response_class=Response,
)
async def content(
    request: Request,
    media_id: str,
    release: Annotated[str | None, Query(max_length=64)] = None,
    lesson_version: Annotated[str | None, Query(max_length=64)] = None,
    authorization: AuthH = None,
    range_header: RangeH = None,
    if_range: IfRangeH = None,
) -> Response:
    mid, rid, lvid = _uuid(media_id), _uuid(release or ""), _uuid(lesson_version or "")
    if _bearer(authorization) is None:
        return _problem(request, _err(401, "unauthenticated", "Authentification requise"))
    if mid is None or rid is None or lvid is None:
        # Contexte pédagogique obligatoire : jamais de diffusion sur la seule foi d'un identifiant.
        return _problem(request, _err(404, *NOT_FOUND))
    return await deliver(request, Target("content", mid, rid, lvid), authorization, range_header, if_range)


@router.get(
    "/{media_id}/preview",
    summary="Aperçu éditorial d'un média sain de la portée (auteurs, relecteurs, éditeurs)",
    response_class=Response,
)
async def preview(
    request: Request,
    media_id: str,
    authorization: AuthH = None,
    range_header: RangeH = None,
    if_range: IfRangeH = None,
) -> Response:
    mid = _uuid(media_id)
    if _bearer(authorization) is None:
        return _problem(request, _err(401, "unauthenticated", "Authentification requise"))
    if mid is None:
        return _problem(request, _err(404, *NOT_FOUND))
    return await deliver(request, Target("preview", mid), authorization, range_header, if_range)

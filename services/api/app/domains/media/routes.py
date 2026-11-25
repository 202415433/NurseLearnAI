"""Téléversement sécurisé des médias (étape 2.2.3-b) — `/api/v1/editorial/media`.

Deux temps (aucun multipart : le corps n'est jamais mis en mémoire ni analysé par le framework) :
  1. `POST /api/v1/editorial/media` (JSON) : réservation atomique du quota en base (0007), débit D16 ;
  2. `PUT  /api/v1/editorial/media/{id}/content` (octets bruts) : lecture EN FLUX vers un fichier
     temporaire, arrêt au-delà de la taille autorisée (413), SHA-256, analyse et normalisation dans un
     processus séparé borné (S4, S5), écriture en QUARANTAINE privée, puis transition `quarantined`.

Les fichiers reçus ne sont JAMAIS diffusables : aucune route ne lit les octets, aucune URL n'est
produite ; l'état `clean` n'est atteignable que par le service antivirus (2.2.3-c).
Identité et portée viennent de la session (Bearer, session sensible), jamais du corps de la requête.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from app.core.config import Settings
from app.core.errors import ProblemError
from app.domains.editorial.errors import BASE
from app.domains.editorial.schemas import I18nText
from app.domains.editorial.scope import can_read, ensure_can_create, not_found, scope_params
from app.domains.editorial.tx import editorial_tx, record
from app.domains.identity.deps import Auth, Db, SensitiveAuth, get_settings_dep, parse_uuid
from app.domains.identity.sessions import AuthContext
from app.domains.media.storage import MediaStorage, StorageUnavailable, random_key

router = APIRouter(prefix="/api/v1/editorial/media", tags=["médias protégés"])
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
log = logging.getLogger("nurselearn.media")

MIB = 1048576
# D15 — identiques aux contraintes de la base (nl_media_max_bytes), unités binaires.
MAX_BYTES = {"image": 5 * MIB, "document": 20 * MIB, "captions": 1 * MIB, "video": 200 * MIB}
MIME_KIND = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "application/pdf": "document",
    "text/vtt": "captions",
    "video/mp4": "video",
}
SANDBOX = Path(__file__).with_name("sandbox.py")
CHUNK = 64 * 1024
DISPLAY_NAME = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,200}$")


def media_problem(status: int, code: str, detail: str, type_: str = "invalid-input") -> ProblemError:
    return ProblemError(status, detail, type_=BASE + type_, extra={"code": code})


# --------------------------------------------------------------------------------------- contrats
class MediaReserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["image", "video", "captions", "document"]
    mime_type: Literal["image/jpeg", "image/png", "image/webp", "application/pdf", "text/vtt", "video/mp4"]
    size_bytes: int = Field(gt=0)
    display_name: str
    license: str = Field(min_length=2, max_length=200)
    attribution: str | None = Field(default=None, max_length=500)
    alt_text: I18nText | None = None
    captions_for: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not DISPLAY_NAME.fullmatch(value):
            raise ValueError("Nom affiché invalide (1 à 200 caractères, sans chemin ni caractère de contrôle)")
        return value

    @model_validator(mode="after")
    def _coherence(self) -> MediaReserveIn:
        if MIME_KIND[self.mime_type] != self.kind:
            raise ValueError("Type MIME incohérent avec le type de média")
        if (self.kind == "image") != (self.alt_text is not None):
            raise ValueError("Texte alternatif obligatoire pour une image, et seulement pour une image")
        if (self.kind == "captions") != (self.captions_for is not None):
            raise ValueError("captions_for obligatoire pour des sous-titres, et seulement pour eux")
        return self


def media_out(row: Any) -> dict[str, Any]:
    """Métadonnées seulement : jamais de clé de stockage, jamais d'URL, jamais d'octets."""
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "uploaded_by": row["uploaded_by"],
        "kind": row["kind"],
        "mime_type": row["mime_type"],
        "state": row["state"],
        "declared_bytes": row["declared_bytes"],
        "byte_size": row["byte_size"],
        "sha256": row["sha256"],
        "display_name": row["display_name"],
        "license": row["license"],
        "attribution": row["attribution"],
        "alt_text": row["alt_text_i18n"],
        "captions_for": row["captions_for"],
        "reservation_expires_at": row["reservation_expires_at"],
        "deleted_at": row["deleted_at"],
        "legal_hold": row["legal_hold"],  # motif réservé à l'historique d'administration (2.2.3-e)
        "access_epoch": row["access_epoch"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# --------------------------------------------------------------------------------------- outils
async def _load(db: AsyncSession, ctx: AuthContext, media_id: uuid.UUID) -> Any:
    row = (await db.execute(text("SELECT * FROM media_assets WHERE id = :id"), {"id": media_id})).mappings().first()
    if row is None or not can_read(ctx, row["organization_id"]):
        raise not_found("Média")
    return row


async def _rate_limited(request: Request, settings: Settings, ctx: AuthContext, org: uuid.UUID | None) -> None:
    """D16, fermé par défaut : sans Redis joignable, aucun téléversement n'est accepté."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise media_problem(503, "media_unavailable", "Service de téléversement indisponible", "service-unavailable")
    hour = int(time.time() // 3600)
    # Espace de noms dérivé de RATE_LIMIT_SALT (ou de la base) : deux piles partageant un Redis (tests,
    # recette) ne consomment jamais les compteurs l'une de l'autre (même règle que la diffusion, 2.2.3-d).
    ns = hashlib.sha256(settings.effective_rate_limit_salt.encode()).hexdigest()[:8]
    checks = [(f"nl:media:rate:{ns}:user:{ctx.user_id}:{hour}", settings.media_uploads_per_hour_user)]
    checks.append((f"nl:media:rate:{ns}:org:{org or 'platform'}:{hour}", settings.media_uploads_per_hour_org))
    try:
        async with redis.pipeline(transaction=True) as pipe:
            for key, _ in checks:
                pipe.incr(key)
                pipe.expire(key, 3700)
            results = await pipe.execute()
    except Exception:
        raise media_problem(
            503, "media_unavailable", "Service de téléversement indisponible", "service-unavailable"
        ) from None
    for (_, limit), count in zip(checks, results[0::2], strict=True):
        if limit > 0 and int(count) > limit:
            raise media_problem(429, "rate_limited", "Trop de téléversements : réessayez plus tard", "rate-limited")


def _limits(memory_mib: int, cpu_seconds: int):  # noqa: ANN202 - fonction de préexécution
    def apply() -> None:
        import resource

        memory = memory_mib * MIB
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * MIB, 256 * MIB))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


async def run_sandbox(settings: Settings, mime: str, src: Path, dst: Path) -> dict[str, Any]:
    """Analyse dans un processus séparé : mémoire, CPU, taille de fichier et durée bornées (S5)."""
    timeout = settings.media_sandbox_timeout_seconds
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(SANDBOX),
        mime,
        str(src),
        str(dst),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
        preexec_fn=_limits(settings.media_sandbox_memory_mib, timeout) if os.name == "posix" else None,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "code": "media_resources", "detail": "Analyse du fichier trop longue"}
    try:
        result = json.loads(out.decode().strip().splitlines()[-1])
    except Exception:
        result = None
    if proc.returncode != 0 or not isinstance(result, dict):
        return {"ok": False, "code": "media_resources", "detail": "Ressources dépassées pendant l'analyse du fichier"}
    return result


# --------------------------------------------------------------------------------------- routes
@router.post("", status_code=201, summary="Réserver un téléversement (quota réservé atomiquement)")
async def reserve(
    body: MediaReserveIn, request: Request, ctx: SensitiveAuth, db: Db, settings: SettingsDep
) -> dict[str, Any]:
    if body.size_bytes > MAX_BYTES[body.kind]:
        raise media_problem(413, "media_too_large", f"Taille maximale : {MAX_BYTES[body.kind] // MIB} Mio", "too-large")
    ensure_can_create(ctx, body.organization_id)
    if body.captions_for is not None:
        target = await _load(db, ctx, body.captions_for)
        if target["kind"] != "video":
            raise media_problem(422, "incoherent_reference", "captions_for doit désigner une vidéo")
    await _rate_limited(request, settings, ctx, body.organization_id)
    # Images (réencodées) et sous-titres (assainis) : taille finale imprévisible, la réservation
    # couvre le maximum du type ; PDF et MP4 sont conservés tels quels : taille annoncée.
    declared = MAX_BYTES[body.kind] if body.kind in ("image", "captions") else body.size_bytes
    media_id = uuid.uuid4()
    async with editorial_tx(db, ctx):
        await db.execute(text("SELECT nl_media_expire_reservations()"))  # nettoyage des réservations échues
        await db.execute(
            text(
                "INSERT INTO media_assets (id, organization_id, uploaded_by, kind, mime_type, declared_bytes, "
                "display_name, license, attribution, alt_text_i18n, captions_for, reservation_expires_at) VALUES "
                "(:id, :org, :by, :kind, :mime, :declared, :name, :license, :attribution, CAST(:alt AS jsonb), "
                ":captions, now() + make_interval(mins => :ttl))"
            ),
            {
                "id": media_id,
                "org": body.organization_id,
                "by": ctx.user_id,
                "kind": body.kind,
                "mime": body.mime_type,
                "declared": declared,
                "name": body.display_name,
                "license": body.license,
                "attribution": body.attribution,
                "alt": json.dumps(body.alt_text.as_json()) if body.alt_text else None,
                "captions": body.captions_for,
                "ttl": settings.media_upload_ttl_minutes,
            },
        )
        await record(
            db,
            request,
            ctx,
            "media.reserved",
            "media_asset",
            media_id,
            body.organization_id,
            kind=body.kind,
            mime_type=body.mime_type,
            declared_bytes=declared,
        )
    row = await _load(db, ctx, media_id)
    return {**media_out(row), "upload_path": f"/api/v1/editorial/media/{media_id}/content"}


@router.put("/{media_id}/content", summary="Téléverser le contenu (flux brut, quarantaine privée)")
async def upload_content(
    media_id: str, request: Request, ctx: SensitiveAuth, db: Db, settings: SettingsDep
) -> dict[str, Any]:
    mid = parse_uuid(media_id)
    row = await _load(db, ctx, mid)
    if row["uploaded_by"] != ctx.user_id:
        raise media_problem(403, "forbidden", "Seul le déposant peut téléverser ce contenu", "forbidden")
    if row["state"] != "pending_upload" or row["deleted_at"] is not None:
        raise media_problem(409, "upload_closed", "Ce téléversement n'est plus ouvert", "editorial-state-conflict")
    limit = int(row["declared_bytes"])
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > limit):
        raise media_problem(413, "media_too_large", f"Taille maximale réservée : {limit} octets", "too-large")

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise media_problem(503, "media_unavailable", "Service de téléversement indisponible", "service-unavailable")
    # Aucune transaction ne reste ouverte pendant la réception (connexion rendue au pool).
    await db.rollback()
    lock_key, token = f"nl:media:upload:{mid}", uuid.uuid4().hex
    try:
        acquired = await redis.set(lock_key, token, nx=True, ex=settings.media_sandbox_timeout_seconds + 900)
    except Exception:
        raise media_problem(
            503, "media_unavailable", "Service de téléversement indisponible", "service-unavailable"
        ) from None
    if not acquired:
        raise media_problem(
            409, "upload_in_progress", "Un téléversement de ce média est déjà en cours", "editorial-state-conflict"
        )

    workdir = Path(tempfile.mkdtemp(prefix="nl-media-", dir=settings.media_tmp_dir))
    os.chmod(workdir, 0o700)
    src, dst = workdir / "received", workdir / "normalized"
    try:
        digest, size = hashlib.sha256(), 0
        try:
            with src.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise media_problem(
                            413, "media_too_large", f"Taille maximale réservée : {limit} octets", "too-large"
                        )
                    digest.update(chunk)
                    out.write(chunk)
        except ClientDisconnect:
            # Interruption réseau : rien n'est stocké, la réservation reste ouverte pour une reprise.
            async with editorial_tx(db, ctx):
                await record(
                    db,
                    request,
                    ctx,
                    "media.upload_interrupted",
                    "media_asset",
                    mid,
                    row["organization_id"],
                    received_bytes=size,
                )
            raise media_problem(400, "upload_interrupted", "Téléversement interrompu : recommencez l'envoi") from None
        if size == 0:
            raise media_problem(422, "media_empty", "Fichier vide")

        result = await run_sandbox(settings, row["mime_type"], src, dst)
        if not result.get("ok"):
            code = str(result.get("code", "media_invalid"))
            async with editorial_tx(db, ctx):
                # Fichier refusé : la réservation est close (abandon), le quota est libéré.
                await db.execute(
                    text(
                        "UPDATE media_assets SET deleted_at = now(), deleted_reason = 'upload_aborted' "
                        "WHERE id = :id AND state = 'pending_upload' AND deleted_at IS NULL"
                    ),
                    {"id": mid},
                )
                await record(db, request, ctx, "media.rejected", "media_asset", mid, row["organization_id"], code=code)
            raise media_problem(422, code, str(result.get("detail", "Fichier refusé")))

        final_size = dst.stat().st_size
        if final_size > limit:
            raise media_problem(413, "media_too_large", "Fichier trop volumineux après normalisation", "too-large")
        final_digest = hashlib.sha256()
        with dst.open("rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                final_digest.update(block)
        sha = final_digest.hexdigest()

        key = random_key()
        try:
            storage = MediaStorage(settings)
            await storage.put_quarantine(key, dst, sha)
        except StorageUnavailable:
            # Rien n'est enregistré en base : la réservation reste ouverte pour une nouvelle tentative.
            async with editorial_tx(db, ctx):
                await record(db, request, ctx, "media.storage_failed", "media_asset", mid, row["organization_id"])
            raise media_problem(
                503, "storage_unavailable", "Stockage des médias indisponible : réessayez", "service-unavailable"
            ) from None

        try:
            async with editorial_tx(db, ctx):
                updated = (
                    await db.execute(
                        text(
                            "UPDATE media_assets SET state = 'quarantined', byte_size = :size, sha256 = :sha, "
                            "quarantine_key = :key WHERE id = :id AND state = 'pending_upload' AND deleted_at IS NULL "
                            "RETURNING id"
                        ),
                        {"id": mid, "size": final_size, "sha": sha, "key": key},
                    )
                ).first()
                if updated is None:
                    raise media_problem(
                        409, "upload_closed", "Ce téléversement n'est plus ouvert", "editorial-state-conflict"
                    )
                await record(
                    db,
                    request,
                    ctx,
                    "media.uploaded",
                    "media_asset",
                    mid,
                    row["organization_id"],
                    kind=row["kind"],
                    mime_type=row["mime_type"],
                    byte_size=final_size,
                    sha256=sha,
                    received_bytes=size,
                    sha256_received=digest.hexdigest(),
                )
        except BaseException:
            # La base a refusé (réservation expirée, conflit) : l'objet tout juste écrit est retiré.
            await storage.delete_quarantine(key)
            raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        try:
            if await redis.get(lock_key) == token.encode():
                await redis.delete(lock_key)
        except Exception:
            log.warning("verrou_televersement_non_libere")
    return media_out(await _load(db, ctx, mid))


MEDIA_STATES = ("pending_upload", "quarantined", "scanning", "clean", "infected", "scan_failed", "revoked")
MediaState = Literal["pending_upload", "quarantined", "scanning", "clean", "infected", "scan_failed", "revoked"]
# Requête de liste : fragments statiques seulement ; les valeurs du client passent en paramètres liés.
LIST_SQL = (
    "SELECT __COLS__ FROM media_assets m "
    "WHERE (CAST(:scope_all AS boolean) OR m.organization_id = ANY(CAST(:scope_orgs AS uuid[]))) "
    "AND (CAST(:q AS text) IS NULL OR m.display_name ILIKE CAST(:q AS text) ESCAPE '\\') "
    "AND (CAST(:kind AS text) IS NULL OR m.kind = CAST(:kind AS text)) "
    "AND (CAST(:state AS text) IS NULL OR m.state = CAST(:state AS text)) "
    "AND (CAST(:deleted AS boolean) OR m.deleted_at IS NULL)__TAIL__"
)
LIST_COUNT = LIST_SQL.replace("__COLS__", "count(*)").replace("__TAIL__", "")
LIST_PAGE = LIST_SQL.replace(
    "__COLS__", "m.*, (SELECT count(*) FROM lesson_version_media l WHERE l.media_id = m.id) AS attachment_count"
).replace("__TAIL__", " ORDER BY m.created_at DESC, m.id LIMIT :limit OFFSET :offset")


def like_pattern(q: str | None) -> str | None:
    """Motif ILIKE littéral : les caractères spéciaux du client sont échappés (aucune injection de motif)."""
    if not q or not q.strip():
        return None
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("", summary="Médias de la portée (métadonnées, jamais les octets ; recherche et filtres 2.2.3-e)")
async def list_media(
    ctx: Auth,
    db: Db,
    response: Response,
    q: Annotated[str | None, Query(max_length=100)] = None,
    kind: Annotated[Literal["image", "video", "captions", "document"] | None, Query()] = None,
    state: Annotated[MediaState | None, Query()] = None,
    include_deleted: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[dict[str, Any]]:
    """Portée calculée côté serveur (jamais transmise par le client). Le total filtré est renvoyé dans
    l'en-tête `X-Total-Count` ; `attachment_count` = nombre de versions de leçon qui rattachent le média.
    Sans paramètre, comportement de 2.2.3-b (500 derniers, supprimés compris)."""
    params: dict[str, Any] = {
        **scope_params(ctx),
        "q": like_pattern(q),
        "kind": kind,
        "state": state,
        "deleted": include_deleted,
        "limit": limit,
        "offset": offset,
    }
    total = (await db.execute(text(LIST_COUNT), params)).scalar() or 0
    rows = await db.execute(text(LIST_PAGE), params)
    response.headers["X-Total-Count"] = str(total)
    return [{**media_out(r), "attachment_count": r["attachment_count"]} for r in rows.mappings()]


@router.get("/{media_id}", summary="Métadonnées et état d'un média")
async def get_media(media_id: str, ctx: Auth, db: Db) -> dict[str, Any]:
    return media_out(await _load(db, ctx, parse_uuid(media_id)))


@router.delete("/{media_id}", status_code=204, summary="Abandonner un téléversement ou supprimer un média (logique)")
async def delete_media(media_id: str, request: Request, ctx: SensitiveAuth, db: Db) -> Response:
    mid = parse_uuid(media_id)
    row = await _load(db, ctx, mid)
    if row["deleted_at"] is not None:
        raise media_problem(409, "already_deleted", "Média déjà supprimé", "editorial-state-conflict")
    reason = "upload_aborted" if row["state"] == "pending_upload" else "user_request"
    async with editorial_tx(db, ctx):
        await db.execute(
            text("UPDATE media_assets SET deleted_at = now(), deleted_reason = :reason WHERE id = :id"),
            {"id": mid, "reason": reason},
        )
        await record(
            db, request, ctx, "media.deleted", "media_asset", mid, row["organization_id"], deleted_reason=reason
        )
    return Response(status_code=204)


# --------------------------------------------------------------------------------------- révocation (2.2.3-d)
class MediaRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=5, max_length=1000)


@router.post(
    "/{media_id}/revoke",
    summary="Révoquer un média : diffusion coupée à la requête suivante (relecteur plateforme ou administration)",
)
async def revoke_media(
    media_id: str, body: MediaRevokeIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    """Passage à `revoked` (irréversible) ; la base incrémente `access_epoch` : toute requête de diffusion
    suivante, et toute revérification d'un flux en cours, est refusée. Objet conservé, jamais diffusé ;
    aucune purge (politique de conservation). Audit sans le motif (texte libre)."""
    mid = parse_uuid(media_id)
    row = await _load(db, ctx, mid)
    if not (ctx.can("content:review", None) or ctx.can("user:manage", None)):
        raise media_problem(
            403, "forbidden", "Révocation réservée aux relecteurs plateforme et à l'administration", "forbidden"
        )
    if row["state"] == "revoked":
        raise media_problem(409, "already_revoked", "Média déjà révoqué", "editorial-state-conflict")
    if row["state"] == "pending_upload":
        raise media_problem(
            409, "transition_forbidden", "Aucun contenu reçu : abandonner le téléversement", "editorial-state-conflict"
        )
    async with editorial_tx(db, ctx):
        epoch = (
            await db.execute(
                text(
                    "UPDATE media_assets SET state = 'revoked', revoked_reason = :reason WHERE id = :id "
                    "RETURNING access_epoch"
                ),
                {"id": mid, "reason": body.reason},
            )
        ).scalar()
        await record(
            db,
            request,
            ctx,
            "media.revoked",
            "media_asset",
            mid,
            row["organization_id"],
            previous_state=row["state"],
            access_epoch=epoch,
        )
    return media_out(await _load(db, ctx, mid))


# --------------------------------------------------------------------------- conservation légale (2.2.3-e)
class LegalHoldIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    hold: bool
    reason: str = Field(min_length=5, max_length=1000)


def _can_hold(ctx: AuthContext, org: uuid.UUID | None) -> bool:
    # Permission dédiée (0010) dans la portée du média ET administration de la plateforme (0007).
    return ctx.can("media:legal_hold", org) and ctx.can("user:manage", None)


@router.post(
    "/{media_id}/legal-hold",
    summary="Poser ou lever une conservation légale (administration, justification obligatoire)",
)
async def set_legal_hold(
    media_id: str, body: LegalHoldIn, request: Request, ctx: SensitiveAuth, db: Db
) -> dict[str, Any]:
    """Passe EXCLUSIVEMENT par la fonction contrôlée `nl_media_set_legal_hold` (0010) : l'API n'a aucun
    droit d'écriture sur les colonnes de conservation. La base revérifie le droit, journalise l'acteur,
    l'horodatage et le motif (journal en ajout seul). La conservation n'ouvre aucun accès : un média
    infecté ou révoqué reste non diffusable. Aucune suppression automatique."""
    mid = parse_uuid(media_id)
    row = await _load(db, ctx, mid)
    if not _can_hold(ctx, row["organization_id"]):
        raise media_problem(
            403, "forbidden", "Conservation légale réservée à l'administration de la plateforme", "forbidden"
        )
    async with editorial_tx(db, ctx):
        event = (
            await db.execute(
                text("SELECT nl_media_set_legal_hold(:m, :h, :r)"), {"m": mid, "h": body.hold, "r": body.reason}
            )
        ).scalar()
        await record(
            db,
            request,
            ctx,
            "media.legal_hold_requested",
            "media_asset",
            mid,
            row["organization_id"],
            legal_hold=body.hold,
            event_id=str(event),
        )
    return media_out(await _load(db, ctx, mid))


@router.get("/{media_id}/legal-hold", summary="Historique des conservations légales (administration)")
async def legal_hold_history(media_id: str, ctx: Auth, db: Db) -> list[dict[str, Any]]:
    mid = parse_uuid(media_id)
    row = await _load(db, ctx, mid)
    if not _can_hold(ctx, row["organization_id"]):
        raise media_problem(403, "forbidden", "Historique réservé à l'administration de la plateforme", "forbidden")
    rows = await db.execute(
        text(
            "SELECT e.id, e.action, e.reason, e.actor_user_id, p.display_name AS actor_name, e.created_at "
            "FROM media_legal_hold_events e LEFT JOIN profiles p ON p.user_id = e.actor_user_id "
            "WHERE e.media_id = :m ORDER BY e.created_at, e.id"
        ),
        {"m": mid},
    )
    return [dict(r) for r in rows.mappings()]

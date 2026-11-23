"""Téléversement sécurisé des médias (étape 2.2.3-b) sur PostgreSQL, Redis et stockage S3 réels.

Stockage : `NL_TEST_S3_ENDPOINT` (RustFS en CI et sous Windows ; serveur S3 local de développement
sinon), identifiants `S3_ACCESS_KEY` / `S3_SECRET_KEY`. Sans point de terminaison, les tests sont
ignorés (comme Mailpit). Comptes et fichiers FICTIFS, générés à la volée ; aucune donnée réelle,
aucune diffusion : seuls l'état `quarantined` et la quarantaine privée sont atteints.
"""

# ruff: noqa: F811 - fixtures pytest importées de test_editorial puis utilisées comme paramètres
from __future__ import annotations

import hashlib
import io
import os
import struct
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from sqlalchemy.engine import Engine
from test_editorial import Acct, _settings, account, make_org, ok, problem_code, sql

from app.main import create_app

M = "/api/v1/editorial/media"
MIB = 1048576
GIB = 1073741824
S3 = os.environ.get("NL_TEST_S3_ENDPOINT")
S3_KEY = os.environ.get("S3_ACCESS_KEY", "nurselearn")
S3_SECRET = os.environ.get("S3_SECRET_KEY", "test-secret-key")
QUARANTINE = "nl-test-media-quarantine"  # compartiment dédié aux tests (jamais celui de l'application)
REAL_S3 = os.environ.get("NL_TEST_S3_KIND", "rustfs") == "rustfs"

pytestmark = pytest.mark.skipif(not S3, reason="NL_TEST_S3_ENDPOINT absent : stockage S3 non disponible")


def media_settings(**overrides: object) -> Any:
    base: dict[str, object] = {
        "s3_endpoint": S3,
        "s3_access_key": S3_KEY,
        "s3_secret_key": S3_SECRET,
        "media_quarantine_bucket": QUARANTINE,
        "media_clean_bucket": "nl-test-media-clean",
        "media_uploads_per_hour_user": 10_000,
        "media_uploads_per_hour_org": 10_000,
    }
    base.update(overrides)
    return _settings(**base)


def s3_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=S3,
        aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@pytest.fixture(scope="module")
def api() -> Iterator[TestClient]:
    with TestClient(create_app(media_settings())) as c:
        yield c


@dataclass
class MWorld:
    org_a: str
    org_b: str
    author: Acct
    org_instr: Acct
    other_instr: Acct
    org_admin: Acct
    student: Acct
    admin: Acct


@pytest.fixture(scope="module")
def mw(api: TestClient, sync_engine: Engine) -> MWorld:
    org_a, org_b = make_org(sync_engine), make_org(sync_engine)
    return MWorld(
        org_a=org_a,
        org_b=org_b,
        author=account(api, sync_engine, ("instructor", None)),
        org_instr=account(api, sync_engine, ("instructor", org_a)),
        other_instr=account(api, sync_engine, ("instructor", org_b)),
        org_admin=account(api, sync_engine, ("org_admin", org_a)),
        student=account(api, sync_engine),
        admin=account(api, sync_engine, ("admin", None)),
    )


# --------------------------------------------------------------------------------------- fichiers
def png(with_metadata: bool = True, size: tuple[int, int] = (64, 48)) -> bytes:
    img = Image.new("RGB", size, (30, 120, 200))
    buf = io.BytesIO()
    if with_metadata:
        exif = Image.Exif()
        exif[0x010F] = "Fabricant fictif"
        exif[0x8825] = {2: (45.0, 30.0, 0.0), 4: (73.0, 35.0, 0.0)}  # coordonnées GPS fictives
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "texte caché")
        img.save(buf, "PNG", pnginfo=info, exif=exif.tobytes())
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 200, 10)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def webp() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 200, 10)).save(buf, "WEBP")
    return buf.getvalue()


def png_with_huge_header() -> bytes:
    """En-tête PNG annonçant 20 000 × 20 000 px (bombe de décompression), sans données réelles."""
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + chunk(b"IEND", b"")
    )


def pdf(js: bool = False, attachment: bool = False, encrypted: bool = False) -> bytes:
    w = PdfWriter()
    w.add_blank_page(200, 200)
    if js:
        # Action d'ouverture JavaScript explicite (add_js est déprécié dans pypdf 6).
        w._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.alert('fictif');")}
        )
    if attachment:
        w.add_attachment("fichier.txt", b"contenu fictif")
    if encrypted:
        w.encrypt("mot-de-passe-fictif")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def pdf_hidden_js() -> bytes:
    """PDF valide + objet JavaScript NON référencé (invisible au parcours structurel)."""
    base = pdf()
    head, _, _ = base.rpartition(b"%%EOF")
    return head + b"%%EOF\n99 0 obj\n<< /S /JavaScript /JS (app.alert(1)) >>\nendobj\n%%EOF\n"


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mp4(codec: bytes = b"avc1", seconds: int = 5, handler: bytes = b"vide") -> bytes:
    mvhd = box(b"mvhd", b"\x00" * 4 + b"\x00" * 8 + struct.pack(">II", 1000, seconds * 1000) + b"\x00" * 80)

    def trak(h: bytes, c: bytes) -> bytes:
        hdlr = box(b"hdlr", b"\x00" * 8 + h + b"\x00" * 12 + b"fictif\x00")
        stsd = box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + box(c, b"\x00" * 70))
        mdia = box(b"mdia", box(b"mdhd", b"\x00" * 24) + hdlr + box(b"minf", box(b"stbl", stsd)))
        return box(b"trak", box(b"tkhd", b"\x00" * 84) + mdia)

    moov = box(b"moov", mvhd + trak(handler, codec) + trak(b"soun", b"mp4a"))
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41") + moov + box(b"mdat", b"\x00" * 256)


VTT = b"WEBVTT\n\n00:00.000 --> 00:02.500\nLavage des mains <b>fictif</b> <script>alert(1)</script>\n"


# --------------------------------------------------------------------------------------- outils
def reserve(api: TestClient, who: Acct, kind: str, mime: str, size: int, **extra: Any) -> Any:
    body: dict[str, Any] = {
        "kind": kind,
        "mime_type": mime,
        "size_bytes": size,
        "display_name": f"fichier-fictif-{uuid.uuid4().hex[:6]}",
        "license": "CC-BY-4.0",
        **extra,
    }
    if kind == "image" and "alt_text" not in extra:
        body["alt_text"] = {"fr": "Illustration fictive"}
    return api.post(M, headers=who.h, json=body)


def put(api: TestClient, who: Acct, media_id: str, data: bytes, **headers: str) -> Any:
    return api.put(
        f"{M}/{media_id}/content",
        headers={**who.h, "Content-Type": "application/octet-stream", **headers},
        content=data,
    )


def uploaded(api: TestClient, who: Acct, kind: str, mime: str, data: bytes, **extra: Any) -> dict:
    media = ok(reserve(api, who, kind, mime, len(data), **extra), 201)
    return ok(put(api, who, media["id"], data))


def quarantine_object(engine: Engine, media_id: str) -> bytes:
    key = sql(engine, "SELECT quarantine_key FROM media_assets WHERE id = :i", i=media_id).scalar()
    return s3_client().get_object(Bucket=QUARANTINE, Key=key)["Body"].read()


def usage(engine: Engine, scope: str, ident: str | None) -> tuple[int, int, int]:
    col = {"user": "user_id", "organization": "organization_id"}[scope]
    row = sql(
        engine,
        f"SELECT used_bytes, reserved_bytes, quota_bytes FROM media_usage_totals WHERE scope_kind = :s AND {col} = :i",
        s=scope,
        i=ident,
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


# =============================================================================================
# Téléversements valides
# =============================================================================================
def test_valid_png_is_reencoded_without_metadata_and_quarantined(
    api: TestClient, mw: MWorld, sync_engine: Engine
) -> None:
    media = ok(reserve(api, mw.author, "image", "image/png", 5000), 201)
    assert media["state"] == "pending_upload" and media["declared_bytes"] == 5 * MIB  # image : maximum réservé
    assert media["upload_path"] == f"{M}/{media['id']}/content"
    done = ok(put(api, mw.author, media["id"], png()))
    assert done["state"] == "quarantined" and done["byte_size"] > 0
    stored = quarantine_object(sync_engine, media["id"])
    assert hashlib.sha256(stored).hexdigest() == done["sha256"]
    with Image.open(io.BytesIO(stored)) as img:
        assert not img.getexif() and "Comment" not in img.info and "exif" not in img.info
    assert b"texte cach" not in stored and b"Fabricant" not in stored
    assert all(k not in done for k in ("quarantine_key", "clean_key")) and "http" not in str(done)


def test_polyglot_image_is_neutralized_by_reencoding(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    # PNG valide suivi d'une archive et de HTML : seul le ré-encodage est stocké, la charge accolée disparaît.
    data = png() + b"PK\x03\x04" + b"\x00" * 32 + b"<html><script>x</script></html>"
    done = uploaded(api, mw.author, "image", "image/png", data)
    stored = quarantine_object(sync_engine, done["id"])
    assert b"PK\x03\x04" not in stored and b"<script" not in stored
    assert stored.endswith(b"IEND\xaeB`\x82")


@pytest.mark.parametrize(
    ("kind", "mime", "factory"),
    [
        ("image", "image/jpeg", jpeg),
        ("image", "image/webp", webp),
        ("document", "application/pdf", pdf),
        ("video", "video/mp4", mp4),
    ],
)
def test_valid_formats(api: TestClient, mw: MWorld, kind: str, mime: str, factory: Any) -> None:
    done = uploaded(api, mw.author, kind, mime, factory())
    assert done["state"] == "quarantined" and done["mime_type"] == mime


def test_valid_vtt_is_sanitized(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    video = uploaded(api, mw.author, "video", "video/mp4", mp4())
    done = uploaded(api, mw.author, "captions", "text/vtt", VTT, captions_for=video["id"])
    stored = quarantine_object(sync_engine, done["id"]).decode()
    assert "<b>fictif</b>" in stored and "<script>" not in stored and "&lt;script&gt;" in stored


# =============================================================================================
# Fichiers invalides, types trompeurs, formats interdits
# =============================================================================================
@pytest.mark.parametrize(
    ("kind", "mime", "data", "code"),
    [
        ("image", "image/png", b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>", "media_type_forbidden"),
        ("image", "image/png", b"<!DOCTYPE html><html><script>x</script></html>", "media_type_forbidden"),
        ("image", "image/png", b"PK\x03\x04" + b"\x00" * 64, "media_type_forbidden"),  # archive ZIP
        ("image", "image/png", b"MZ\x90\x00" + b"\x00" * 64, "media_type_forbidden"),  # exécutable
        ("image", "image/png", jpeg(), "media_type_mismatch"),  # JPEG annoncé PNG
        ("document", "application/pdf", png(), "media_type_mismatch"),
        ("image", "image/png", png()[:60], "image_invalid"),  # image tronquée
        ("image", "image/png", png_with_huge_header(), "image_dimensions"),  # bombe de décompression
        ("document", "application/pdf", pdf(js=True), "pdf_active_content"),
        ("document", "application/pdf", pdf(attachment=True), "pdf_active_content"),
        ("document", "application/pdf", pdf(encrypted=True), "pdf_encrypted"),
        ("document", "application/pdf", b"%PDF-1.7\n" + b"x" * 200, "pdf_invalid"),
        ("document", "application/pdf", pdf() + b"PK\x03\x04" + b"\x00" * 64, "pdf_invalid"),  # polyglotte PDF+ZIP
        ("document", "application/pdf", pdf_hidden_js(), "pdf_active_content"),  # objet actif non référencé
        ("video", "video/mp4", mp4()[:-40], "mp4_invalid"),  # boîte tronquée
        ("video", "video/mp4", mp4(codec=b"encv"), "mp4_codec"),  # flux chiffré
        ("video", "video/mp4", mp4(seconds=4 * 3600), "mp4_too_long"),
        ("video", "video/mp4", mp4(handler=b"meta"), "mp4_invalid"),
        ("captions", "text/vtt", b"00:00.000 --> 00:01.000\nsans en-tete\n", "media_type_forbidden"),
        ("captions", "text/vtt", b"WEBVTT\n\nSTYLE\n::cue { color: red }\n", "vtt_invalid"),
        ("captions", "text/vtt", b"WEBVTT\n\n00:00 --> 00:01\nminutage invalide\n", "vtt_invalid"),
        ("captions", "text/vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\n\xff\xfe\n", "vtt_invalid"),
    ],
)
def test_invalid_files_are_rejected_and_reservation_released(
    api: TestClient, mw: MWorld, sync_engine: Engine, kind: str, mime: str, data: bytes, code: str
) -> None:
    extra: dict[str, Any] = {}
    if kind == "captions":
        extra["captions_for"] = uploaded(api, mw.author, "video", "video/mp4", mp4())["id"]
    media = ok(reserve(api, mw.author, kind, mime, max(len(data), 1), **extra), 201)
    before = usage(sync_engine, "user", mw.author.user_id)
    r = put(api, mw.author, media["id"], data)
    assert r.status_code == 422 and problem_code(r) == code, r.text
    row = sql(
        sync_engine, "SELECT state, deleted_reason, quarantine_key FROM media_assets WHERE id = :i", i=media["id"]
    ).one()
    assert tuple(row) == ("pending_upload", "upload_aborted", None)  # rien stocké, réservation close
    after = usage(sync_engine, "user", mw.author.user_id)
    reserved = {"image": 5 * MIB, "captions": MIB}.get(kind, max(len(data), 1))
    assert after[1] == before[1] - reserved
    assert put(api, mw.author, media["id"], data).status_code == 409


def test_forbidden_declared_types_and_incoherent_contracts(api: TestClient, mw: MWorld) -> None:
    for mime in ("image/svg+xml", "text/html", "application/zip", "application/x-msdownload", "image/gif"):
        assert reserve(api, mw.author, "image", mime, 10).status_code == 422
    assert reserve(api, mw.author, "document", "image/png", 10).status_code == 422  # type incohérent
    assert reserve(api, mw.author, "image", "image/png", 10, alt_text=None).status_code == 422  # alt obligatoire
    assert reserve(api, mw.author, "captions", "text/vtt", 10).status_code == 422  # captions_for absent
    bad = reserve(api, mw.author, "document", "application/pdf", 10, uploaded_by=mw.admin.user_id)
    assert bad.status_code == 422  # identité jamais fournie par le corps
    assert (
        reserve(api, mw.author, "document", "application/pdf", 10, display_name="../../etc/passwd").status_code == 422
    )


# =============================================================================================
# Tailles
# =============================================================================================
def test_size_limits(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    for kind, mime, limit in (
        ("image", "image/png", 5 * MIB),
        ("document", "application/pdf", 20 * MIB),
        ("captions", "text/vtt", MIB),
        ("video", "video/mp4", 200 * MIB),
    ):
        extra = (
            {"captions_for": uploaded(api, mw.author, "video", "video/mp4", mp4())["id"]} if kind == "captions" else {}
        )
        r = reserve(api, mw.author, kind, mime, limit + 1, **extra)
        assert r.status_code == 413 and problem_code(r) == "media_too_large"
        at_limit = ok(reserve(api, mw.author, kind, mime, limit, **extra), 201)  # limite exacte acceptée
        assert api.delete(f"{M}/{at_limit['id']}", headers=mw.author.h).status_code == 204
    media = ok(reserve(api, mw.author, "document", "application/pdf", 1000), 201)
    r = put(api, mw.author, media["id"], pdf() + b"\n" * 2000)  # plus que la taille réservée
    assert r.status_code == 413 and problem_code(r) == "media_too_large"
    r = put(api, mw.author, media["id"], b"%PDF", **{"Content-Length": str(10**9)})
    assert r.status_code == 413
    assert sql(sync_engine, "SELECT state FROM media_assets WHERE id = :i", i=media["id"]).scalar() == "pending_upload"
    assert put(api, mw.author, media["id"], b"").status_code in (409, 422)


# =============================================================================================
# Permissions, établissements, sessions
# =============================================================================================
def test_permissions_and_establishment_isolation(api: TestClient, mw: MWorld) -> None:
    assert reserve(api, mw.student, "document", "application/pdf", 10).status_code == 403
    assert reserve(api, mw.org_instr, "document", "application/pdf", 10).status_code == 403  # public : global
    assert reserve(api, mw.org_admin, "document", "application/pdf", 10, organization_id=mw.org_a).status_code == 403
    assert reserve(api, mw.other_instr, "document", "application/pdf", 10, organization_id=mw.org_a).status_code == 404
    media = ok(reserve(api, mw.org_instr, "document", "application/pdf", 5000, organization_id=mw.org_a), 201)
    for who in (mw.other_instr, mw.student, mw.org_admin):
        assert api.get(f"{M}/{media['id']}", headers=who.h).status_code == 404
        assert put(api, who, media["id"], pdf()).status_code == 404
        assert media["id"] not in {x["id"] for x in ok(api.get(M, headers=who.h))}
    # Seul le déposant téléverse, même pour un utilisateur de la même portée globale.
    other_author = ok(reserve(api, mw.author, "document", "application/pdf", 5000), 201)
    assert put(api, mw.admin, other_author["id"], pdf()).status_code == 403
    assert api.get(f"{M}/pas-un-uuid", headers=mw.author.h).status_code == 404
    assert api.post(M, json={}).status_code == 401
    assert api.put(f"{M}/{media['id']}/content", content=b"x").status_code == 401


def test_stale_session_cannot_upload(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    acct = account(api, sync_engine, ("instructor", None))
    media = ok(reserve(api, acct, "document", "application/pdf", 5000), 201)
    sql(
        sync_engine,
        "UPDATE auth_sessions SET last_seen_at = now() - interval '31 minutes' WHERE user_id = :u",
        u=acct.user_id,
    )
    r = put(api, acct, media["id"], pdf())
    assert r.status_code == 403 and r.json()["type"].endswith("reauthentication-required")
    assert reserve(api, acct, "document", "application/pdf", 10).status_code == 403


# =============================================================================================
# Quotas, concurrence, libérations, performance
# =============================================================================================
def test_quota_reached_and_released(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    acct = account(api, sync_engine, ("instructor", None))
    ids = [ok(reserve(api, acct, "video", "video/mp4", 200 * MIB), 201)["id"] for _ in range(5)]
    r = reserve(api, acct, "video", "video/mp4", 200 * MIB)
    assert r.status_code == 413 and problem_code(r) == "quota_exceeded"
    assert usage(sync_engine, "user", acct.user_id) == (0, 1000 * MIB, GIB)
    assert api.delete(f"{M}/{ids[0]}", headers=acct.h).status_code == 204  # abandon : libération
    assert usage(sync_engine, "user", acct.user_id) == (0, 800 * MIB, GIB)
    ok(reserve(api, acct, "video", "video/mp4", 200 * MIB), 201)
    used, reserved, quota = usage(sync_engine, "user", acct.user_id)
    assert used >= 0 and reserved >= 0 and used + reserved <= quota


def test_concurrent_reservations_respect_the_organization_quota(
    api: TestClient, mw: MWorld, sync_engine: Engine
) -> None:
    org = make_org(sync_engine)
    users = [account(api, sync_engine, ("instructor", org)) for _ in range(6)]
    ok(reserve(api, users[0], "document", "application/pdf", 1, organization_id=org), 201)
    with sync_engine.begin() as conn:  # quota réduit par un administrateur (acteur déclaré, audité)
        from sqlalchemy import text

        conn.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": mw.admin.user_id})
        conn.execute(
            text("UPDATE media_usage SET quota_bytes = :q WHERE organization_id = :o"), {"q": 3 * MIB + 1, "o": org}
        )
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(
            pool.map(
                lambda u: reserve(api, u, "document", "application/pdf", MIB, organization_id=org).status_code, users
            )
        )
    assert sorted(codes) == [201, 201, 201, 413, 413, 413]
    used, reserved, quota = usage(sync_engine, "organization", org)
    assert used + reserved == 3 * MIB + 1 <= quota and used >= 0 and reserved >= 0


def test_occupation_recalculation_performance(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    """2 000 médias dans une portée (quota maximal prévu par établissement) : coût d'une réservation."""
    org = make_org(sync_engine)
    acct = account(api, sync_engine, ("instructor", org))
    from sqlalchemy import text

    with sync_engine.begin() as conn:
        conn.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": acct.user_id})
        conn.execute(
            text(
                "INSERT INTO media_assets (organization_id, uploaded_by, kind, mime_type, declared_bytes, display_name, "
                "license, reservation_expires_at) SELECT :o, :u, 'document', 'application/pdf', 100, 'perf-' || g, "
                "'CC0', now() + interval '1 hour' FROM generate_series(1, 2000) g"
            ),
            {"o": org, "u": acct.user_id},
        )
    started = time.perf_counter()
    for _ in range(5):
        ok(reserve(api, acct, "document", "application/pdf", 100, organization_id=org), 201)
    per_request_ms = (time.perf_counter() - started) / 5 * 1000
    with sync_engine.begin() as conn:
        plan_start = time.perf_counter()
        conn.execute(text("SELECT * FROM nl_media_scope_usage('organization', NULL, :o)"), {"o": org}).one()
        scope_ms = (time.perf_counter() - plan_start) * 1000
    print(f"\nperformance : réservation {per_request_ms:.1f} ms ; calcul d'occupation {scope_ms:.1f} ms (2 005 médias)")
    assert scope_ms < 200 and per_request_ms < 1500


def test_rate_limit_per_user(sync_engine: Engine) -> None:
    with TestClient(create_app(media_settings(media_uploads_per_hour_user=2))) as c:
        acct = account(c, sync_engine, ("instructor", None))
        codes = [reserve(c, acct, "document", "application/pdf", 10).status_code for _ in range(3)]
        assert codes == [201, 201, 429]


# =============================================================================================
# Interruptions, reprises, stockage indisponible
# =============================================================================================
def test_storage_unavailable_keeps_reservation_for_retry(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    with TestClient(create_app(media_settings(s3_endpoint="http://127.0.0.1:9"))) as broken:
        login = ok(
            broken.post(
                "/api/v1/auth/login", json={"email": mw.author.email, "password": "Une phrase de passe solide 2026"}
            )
        )
        who = Acct(mw.author.email, login["session_token"], mw.author.user_id)
        media = ok(reserve(broken, who, "document", "application/pdf", 5000), 201)
        r = put(broken, who, media["id"], pdf())
        assert r.status_code == 503 and problem_code(r) == "storage_unavailable"
        assert "127.0.0.1" not in r.text
    assert sql(sync_engine, "SELECT state FROM media_assets WHERE id = :i", i=media["id"]).scalar() == "pending_upload"
    assert ok(put(api, mw.author, media["id"], pdf()))["state"] == "quarantined"  # reprise réussie


def test_interrupted_upload_stores_nothing_and_can_resume(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    media = ok(reserve(api, mw.author, "document", "application/pdf", 5000), 201)
    app = api.app
    messages = [
        {"type": "http.request", "body": b"%PDF-1.7\n", "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent: list[dict] = []

    async def receive() -> dict:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": f"{M}/{media['id']}/content",
        "raw_path": f"{M}/{media['id']}/content".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", f"Bearer {mw.author.token}".encode()),
            (b"content-type", b"application/octet-stream"),
            (b"host", b"testserver"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    api.portal.call(app, scope, receive, send)  # même boucle que le client de test (pool de connexions)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert status == 400
    row = sql(
        sync_engine, "SELECT state, quarantine_key, deleted_at FROM media_assets WHERE id = :i", i=media["id"]
    ).one()
    assert tuple(row) == ("pending_upload", None, None)
    assert ok(put(api, mw.author, media["id"], pdf()))["state"] == "quarantined"


def test_parallel_upload_of_the_same_media_is_refused(api: TestClient, mw: MWorld) -> None:
    import redis as redis_sync

    media = ok(reserve(api, mw.author, "document", "application/pdf", 5000), 201)
    client = redis_sync.Redis.from_url(os.environ["REDIS_URL"])
    client.set(f"nl:media:upload:{media['id']}", "autre-envoi", ex=30)
    try:
        r = put(api, mw.author, media["id"], pdf())
        assert r.status_code == 409 and problem_code(r) == "upload_in_progress"
    finally:
        client.delete(f"nl:media:upload:{media['id']}")
    assert ok(put(api, mw.author, media["id"], pdf()))["state"] == "quarantined"


# =============================================================================================
# Absence de diffusion, audit
# =============================================================================================
def test_nothing_is_ever_served(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    done = uploaded(api, mw.author, "document", "application/pdf", pdf())
    paths = api.app.openapi()["paths"]  # type: ignore[attr-defined]
    media_ops = {(m.upper(), p) for p, item in paths.items() if p.startswith(M) for m in item}
    assert media_ops == {
        ("POST", M),
        ("GET", M),
        ("PUT", f"{M}/{{media_id}}/content"),
        ("GET", f"{M}/{{media_id}}"),
        ("DELETE", f"{M}/{{media_id}}"),
    }
    assert not any("/api/v1/media" in p for p in paths)  # aucune diffusion (2.2.3-d)
    body = api.get(f"{M}/{done['id']}", headers=mw.author.h)
    assert body.headers["content-type"].startswith("application/json") and "key" not in body.text.lower()
    # Le téléversement seul ne produit jamais un média sain ; depuis 2.2.3-c, seul un verdict antivirus
    # traçable (même tentative, mêmes octets) le peut.
    assert sql(sync_engine, "SELECT state FROM media_assets WHERE id = :i", i=done["id"]).scalar() == "quarantined"
    untraceable = (
        "SELECT count(*) FROM media_assets m WHERE m.state = 'clean' AND NOT EXISTS (SELECT 1 FROM media_scan_events e "
        "WHERE e.media_id = m.id AND e.attempt = m.scan_attempt AND e.result = 'clean' AND e.sha256 = m.sha256)"
    )
    assert sql(sync_engine, untraceable).scalar() == 0
    if REAL_S3:  # stockage réel : la quarantaine refuse toute lecture anonyme
        import urllib.error
        import urllib.request

        key = sql(sync_engine, "SELECT quarantine_key FROM media_assets WHERE id = :i", i=done["id"]).scalar()
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{S3}/{QUARANTINE}/{key}", timeout=5)  # noqa: S310 - test de non-exposition
        assert exc.value.code in (401, 403)


def test_audit_without_sensitive_data(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    name = f"nom-confidentiel-{uuid.uuid4().hex[:8]}"
    media = ok(reserve(api, mw.author, "document", "application/pdf", 5000, display_name=name), 201)
    ok(put(api, mw.author, media["id"], pdf()))
    bad = ok(reserve(api, mw.author, "document", "application/pdf", 5000), 201)
    put(api, mw.author, bad["id"], pdf(js=True))
    rows = sql(
        sync_engine,
        "SELECT action, metadata::text AS m, actor_user_id FROM audit_logs WHERE entity_id IN (:a, :b) ORDER BY id",
        a=media["id"],
        b=bad["id"],
    ).all()
    actions = [r.action for r in rows]
    assert actions == ["media.reserved", "media.uploaded", "media.reserved", "media.rejected"]
    key = sql(sync_engine, "SELECT quarantine_key FROM media_assets WHERE id = :i", i=media["id"]).scalar()
    for r in rows:
        assert name not in r.m and key not in r.m and "token" not in r.m.lower() and "app.alert" not in r.m
        assert str(r.actor_user_id) == mw.author.user_id
    assert '"code": "pdf_active_content"' in rows[3].m


def test_stored_pdf_is_identical_and_valid(api: TestClient, mw: MWorld, sync_engine: Engine) -> None:
    data = pdf()
    done = uploaded(api, mw.author, "document", "application/pdf", data)
    stored = quarantine_object(sync_engine, done["id"])
    assert stored == data and len(PdfReader(io.BytesIO(stored)).pages) == 1

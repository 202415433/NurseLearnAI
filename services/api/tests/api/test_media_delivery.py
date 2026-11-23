"""Diffusion sécurisée des médias (étape 2.2.3-d) sur PostgreSQL, Redis, stockage S3 et ClamAV RÉELS.

Chaîne complète, sans raccourci : téléversement par l'API (quarantaine), analyse par le worker
(ClamAV réel en CI et sous Windows ; imitation locale seulement si NL_TEST_CLAMD_KIND=fake),
rattachement à un brouillon de leçon, workflow éditorial (soumission, approbation, publication d'une
édition PUBLIQUE et NON MÉDICALE), puis diffusion par le service SÉPARÉ `create_delivery_app`, qui
n'utilise que l'identité S3 de LECTURE (RustFS : identité de test `nl-test-nl-media-reader`).

Contenus et comptes FICTIFS (`@example.test`) ; aucune publication médicale.
"""

# ruff: noqa: F811 - fixtures pytest importées puis utilisées comme paramètres
from __future__ import annotations

import os
import secrets
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from test_editorial import (  # noqa: F401 - fixtures partagées
    Acct,
    E,
    World,
    account,
    approve,
    new_lesson,
    new_module,
    new_program,
    new_version,
    ok,
    sql,
    submit,
    w,
)
from test_media_scan import (  # noqa: F401 - fixtures partagées
    CLAMD_HOST,
    CLAMD_KIND,
    REDIS,
    clamd,
    eicar,
    make,
    planted,
    scanner_dsn,
)
from test_media_upload import (  # noqa: F401 - fixtures partagées
    QUARANTINE,
    REAL_S3,
    S3,
    S3_KEY,
    S3_SECRET,
    M,
    api,
    box,
    media_settings,
    pdf,
    png,
    put,
    reserve,
    s3_client,
    uploaded,
)

from app.cli import media_legal_hold
from app.domains.media import delivery
from app.domains.media.delivery import parse_range, safe_filename
from app.main import create_app, create_delivery_app
from app.ops.s3_provision import READER_USER, Admin, provision

CLEAN = "nl-test-media-clean"
PREFIX = "nl-test-"
D = "/api/v1/media"

pytestmark = pytest.mark.skipif(
    not (S3 and REDIS and (CLAMD_HOST or CLAMD_KIND == "fake")),
    reason="diffusion des médias : S3, Redis et ClamAV (NL_TEST_CLAMD_HOST) requis",
)


def big_mp4(mdat_bytes: int = 300_000) -> bytes:
    """MP4 fictif valide (piste vidéo H.264 + piste audio), données de taille choisie (Range, flux)."""
    import struct

    mvhd = box(b"mvhd", b"\x00" * 4 + b"\x00" * 8 + struct.pack(">II", 1000, 5000) + b"\x00" * 80)

    def trak(h: bytes, c: bytes) -> bytes:
        hdlr = box(b"hdlr", b"\x00" * 8 + h + b"\x00" * 12 + b"fictif\x00")
        stsd = box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + box(c, b"\x00" * 70))
        mdia = box(b"mdia", box(b"mdhd", b"\x00" * 24) + hdlr + box(b"minf", box(b"stbl", stsd)))
        return box(b"trak", box(b"tkhd", b"\x00" * 84) + mdia)

    moov = box(b"moov", mvhd + trak(b"vide", b"avc1") + trak(b"soun", b"mp4a"))
    payload = bytes(i % 251 for i in range(mdat_bytes))  # motif non nul : vérification octet par octet
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41") + moov + box(b"mdat", payload)


VTT = b"WEBVTT\n\n00:00.000 --> 00:02.500\nLavage des mains fictif\n"


# ============================================================================================ fixtures
@pytest.fixture(scope="module")
def worker(scanner_dsn: str, clamd: tuple[str, int]) -> Iterator[Any]:
    """Worker d'analyse réel (connexion de service nl_media_scanner), partagé par le module."""
    wk = make(scanner_dsn, clamd)
    yield wk
    wk.reset()


@pytest.fixture(scope="module")
def reader_creds() -> tuple[str, str]:
    """Identité S3 de LECTURE. RustFS : identité de test provisionnée (politique nl-media-reader) ;
    serveur S3 de mise au point (sans IAM) : identifiants du serveur."""
    if not REAL_S3:
        return S3_KEY, S3_SECRET
    key = secrets.token_hex(16)
    result = provision(Admin(S3 or "", S3_KEY, S3_SECRET), {READER_USER: key}, QUARANTINE, CLEAN, prefix=PREFIX)
    assert result["ok"], result
    return f"{PREFIX}{READER_USER}", key


def delivery_settings(reader: tuple[str, str], **overrides: Any) -> Any:
    base: dict[str, Any] = {
        "s3_access_key": reader[0],
        "s3_secret_key": reader[1],
        "media_clean_bucket": CLEAN,
        "media_delivery_recheck_seconds": 30,
        "media_delivery_chunk_bytes": 16 * 1024,
        "media_delivery_content_per_minute_user": 10_000,
        "media_delivery_preview_per_minute_user": 10_000,
        "media_delivery_per_minute_org": 100_000,
        "media_delivery_range_per_minute_media": 10_000,
        "media_delivery_bytes_per_minute_user": 1 << 40,
        "media_delivery_concurrent_streams_user": 50,
    }
    base.update(overrides)
    return media_settings(**base)


def delivery_client(reader: tuple[str, str], **overrides: Any) -> TestClient:
    return TestClient(create_delivery_app(delivery_settings(reader, **overrides)), raise_server_exceptions=False)


@pytest.fixture(scope="module")
def dl(reader_creds: tuple[str, str]) -> Iterator[TestClient]:
    with delivery_client(reader_creds) as c:
        yield c


@dataclass
class Pub:
    release: str
    lv: str
    image: str
    image_bytes: bytes
    video: str
    video_bytes: bytes
    captions: str
    pdf: str
    unattached: str
    org_media: str
    org_release: str
    org_lv: str
    student: Acct

    def url(self, media: str, lv: str | None = None, release: str | None = None) -> str:
        return f"{D}/{media}/content?release={release or self.release}&lesson_version={lv or self.lv}"


def clean_media(api: TestClient, worker: Any, who: Acct, kind: str, mime: str, data: bytes, **extra: Any) -> str:
    mid = uploaded(api, who, kind, mime, data, **extra)["id"]
    assert worker.process(mid) == "clean"
    return mid


def attach(engine: Engine, actor: str, lv: str, media: str) -> None:
    """Rattachement d'un média à un BROUILLON (aucune route d'API en 2.2.3-d : connexion d'administration,
    acteur déclaré ; la base vérifie le droit, l'état brouillon, la portée et l'état du média)."""
    with engine.begin() as c:
        c.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": actor})
        c.execute(
            text("INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES (:l, :m, :a)"),
            {"l": lv, "m": media, "a": actor},
        )


PUBLISHED: list[tuple[str, Acct]] = []  # éditions publiées par ce module, archivées à la fin


@pytest.fixture(scope="module", autouse=True)
def archive_published(api: TestClient) -> Iterator[None]:
    """L'historique éditorial est immuable : les éditions de test sont ARCHIVÉES (programmes retirés
    du catalogue), jamais supprimées — comme les autres tests du catalogue."""
    yield
    while PUBLISHED:
        rid, reviewer = PUBLISHED.pop()
        ok(api.post(f"{E}/releases/{rid}/archive", headers=reviewer.h, json={"reason": "Fin du test de diffusion"}))


def publish_with_media(
    api: TestClient,
    w: World,
    engine: Engine,
    author: Acct,
    media: list[str],
    org: str | None = None,
    reviewer: Acct | None = None,
) -> tuple[str, str, str]:
    reviewer = reviewer or w.reviewer
    prog = new_program(api, w, author, org=org)
    mod = new_module(api, w, author, org=org)
    les = new_lesson(api, author, mod["id"])
    lv = ok(new_version(api, author, les["id"]), 201)["id"]
    for m in media:
        attach(engine, author.user_id, lv, m)
    ok(submit(api, author, "lesson-versions", lv))
    ok(approve(api, reviewer, "lesson-versions", lv))
    items = [{"lesson_id": les["id"], "position": 0, "versions": {"fr": lv}}]
    mv = ok(api.post(f"{E}/modules/{mod['id']}/versions", headers=author.h, json={"lessons": items}), 201)
    ok(submit(api, author, "module-versions", mv["id"]))
    ok(approve(api, reviewer, "module-versions", mv["id"]))
    rel = ok(
        api.post(
            f"{E}/programs/{prog['id']}/releases",
            headers=author.h,
            json={
                "modules": [{"module_version_id": mv["id"], "position": 0}],
                "prerequisites": [],
                "change_summary": "Édition fictive (test diffusion)",
            },
        ),
        201,
    )
    ok(submit(api, author, "releases", rel["id"]))
    ok(approve(api, reviewer, "releases", rel["id"]))
    ok(api.post(f"{E}/releases/{rel['id']}/publish", headers=reviewer.h))
    PUBLISHED.append((rel["id"], reviewer))
    return rel["id"], lv, prog["id"]


@pytest.fixture(scope="module")
def pub(api: TestClient, w: World, sync_engine: Engine, worker: Any) -> Pub:
    image_bytes = png(with_metadata=False, size=(31, 17))
    image = clean_media(api, worker, w.author, "image", "image/png", image_bytes)
    video_bytes = big_mp4()
    video = clean_media(api, worker, w.author, "video", "video/mp4", video_bytes)
    captions = clean_media(api, worker, w.author, "captions", "text/vtt", VTT, captions_for=video)
    document = clean_media(
        api, worker, w.author, "document", "application/pdf", pdf(), display_name='Fiche "pratique" .. été <x>.exe'
    )
    unattached = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False))
    org_media = clean_media(
        api, worker, w.org_instr, "image", "image/png", png(with_metadata=False), organization_id=w.org_a
    )
    release, lv, _ = publish_with_media(api, w, sync_engine, w.author, [image, video, document])
    org_release, org_lv, _ = publish_with_media(
        api, w, sync_engine, w.org_instr, [org_media], org=w.org_a, reviewer=w.org_rev
    )
    # Les octets réellement stockés (images ré-encodées, PDF reconstruits) servent de référence.
    stored = {
        m: s3_client()
        .get_object(
            Bucket=CLEAN, Key=sql(sync_engine, "SELECT clean_key FROM media_assets WHERE id = :i", i=m).scalar()
        )["Body"]
        .read()
        for m in (image, video)
    }
    return Pub(
        release=release,
        lv=lv,
        image=image,
        image_bytes=stored[image],
        video=video,
        video_bytes=stored[video],
        captions=captions,
        pdf=document,
        unattached=unattached,
        org_media=org_media,
        org_release=org_release,
        org_lv=org_lv,
        student=w.student,
    )


def code_of(r: Any) -> str:
    assert r.headers["content-type"].startswith("application/problem+json"), (r.status_code, r.headers)
    return r.json()["type"].rsplit("/", 1)[-1]


def assert_safe_headers(r: Any) -> None:
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["cache-control"] == "private, no-store"
    assert "sandbox" in h["content-security-policy"] and "default-src 'none'" in h["content-security-policy"]
    assert h["cross-origin-resource-policy"] == "same-site"
    assert "location" not in h
    for value in h.values():  # aucune adresse de stockage, aucune signature
        assert "9000" not in value and "X-Amz" not in value and "nl-media" not in value


# =============================================================================================
# 1. Accès autorisé ; en-têtes par format
# =============================================================================================
def test_authorized_learner_gets_clean_image_with_safe_headers(dl: TestClient, pub: Pub, sync_engine: Engine) -> None:
    r = dl.get(pub.url(pub.image), headers=pub.student.h)
    assert r.status_code == 200, r.text
    assert r.content == pub.image_bytes
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["accept-ranges"] == "none"
    assert int(r.headers["content-length"]) == len(pub.image_bytes)
    assert_safe_headers(r)
    # Audit : une ligne par minute, utilisateur et média ; jamais de jeton ni d'adresse de stockage.
    dl.get(pub.url(pub.image), headers=pub.student.h)
    rows = sql(
        sync_engine,
        "SELECT metadata::text FROM audit_logs WHERE action = 'media.access_granted' AND entity_id = :m "
        "AND actor_user_id = :u",
        m=pub.image,
        u=pub.student.user_id,
    ).all()
    assert len(rows) == 1
    assert pub.student.token not in rows[0][0] and "clean_key" not in rows[0][0] and "nl-media" not in rows[0][0]


def test_pdf_is_an_attachment_with_a_sanitized_name(dl: TestClient, pub: Pub) -> None:
    r = dl.get(pub.url(pub.pdf), headers=pub.student.h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    name = disposition.split('filename="', 1)[1].rstrip('"')
    assert name == "Fiche-pratique-ete-x.pdf", name
    assert all(ch.isalnum() or ch in "._-" for ch in name), name
    assert ".." not in name and "/" not in name and '"' not in name
    assert r.content.startswith(b"%PDF")
    assert_safe_headers(r)


def test_captions_of_an_attached_video_are_served_as_strict_vtt(dl: TestClient, pub: Pub) -> None:
    r = dl.get(pub.url(pub.captions), headers=pub.student.h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/vtt; charset=utf-8"
    assert r.content.startswith(b"WEBVTT") and b"<script" not in r.content
    assert r.headers["content-disposition"].startswith("inline;")
    assert_safe_headers(r)


def test_safe_filename_and_range_parser_units() -> None:
    assert safe_filename('../../a"b<c>.exe', "pdf") == "a-b-c.pdf"
    assert safe_filename("rapport.pdf.exe", "pdf") == "rapport-pdf.pdf"
    assert safe_filename("été déjà", "png") == "ete-deja.png"
    assert safe_filename("...", "mp4") == "media.mp4"
    assert parse_range(None, 100, 10) is None
    assert parse_range("bytes=0-9", 100, 50) == (0, 9)
    assert parse_range("bytes=90-", 100, 50) == (90, 99)
    assert parse_range("bytes=0-", 100, 10) == (0, 9)  # plage ouverte bornée
    assert parse_range("bytes=-10", 100, 50) == (90, 99)
    assert parse_range("bytes=95-500", 100, 50) == (95, 99)
    for bad in (
        "bytes=100-",
        "bytes=5-1",
        "bytes=-0",
        "bytes=-",
        "bytes=0-1,2-3",
        "items=0-1",
        "bytes=a-b",
        "bytes=1-2-3",
    ):
        with pytest.raises(delivery.DeliveryError) as exc:
            parse_range(bad, 100, 50)
        assert exc.value.status == 416 and exc.value.headers["Content-Range"] == "bytes */100"


# =============================================================================================
# 14–16. HTTP Range
# =============================================================================================
def test_video_range_requests(dl: TestClient, pub: Pub) -> None:
    size = len(pub.video_bytes)
    h = pub.student.h
    full = dl.get(pub.url(pub.video), headers=h)
    assert full.status_code == 200 and full.content == pub.video_bytes and full.headers["accept-ranges"] == "bytes"
    etag = full.headers["etag"]
    r = dl.get(pub.url(pub.video), headers={**h, "Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == pub.video_bytes[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{size}" and r.headers["content-length"] == "100"
    r = dl.get(pub.url(pub.video), headers={**h, "Range": "bytes=-50"})
    assert r.status_code == 206 and r.content == pub.video_bytes[-50:]
    r = dl.get(pub.url(pub.video), headers={**h, "Range": f"bytes={size - 10}-"})
    assert r.status_code == 206 and r.content == pub.video_bytes[-10:]
    r = dl.get(pub.url(pub.video), headers={**h, "Range": "bytes=1000-2000", "If-Range": etag})
    assert r.status_code == 206 and r.content == pub.video_bytes[1000:2001]
    r = dl.get(pub.url(pub.video), headers={**h, "Range": "bytes=1000-2000", "If-Range": '"autre"'})
    assert r.status_code == 200 and r.content == pub.video_bytes  # validateur différent : représentation complète
    assert_safe_headers(r)


def test_open_range_is_bounded(reader_creds: tuple[str, str], pub: Pub) -> None:
    with delivery_client(reader_creds, media_delivery_max_open_range_bytes=1048576) as c:
        r = c.get(pub.url(pub.video), headers={**pub.student.h, "Range": "bytes=0-"})
    assert r.status_code == 206
    assert len(r.content) == min(len(pub.video_bytes), 1048576)


def test_invalid_and_multiple_ranges_are_refused(dl: TestClient, pub: Pub) -> None:
    size = len(pub.video_bytes)
    for bad in (f"bytes={size}-", "bytes=500-10", "bytes=0-1,5-9", "bytes=0-0,1-1,2-2,3-3", "items=0-10", "bytes=x-"):
        r = dl.get(pub.url(pub.video), headers={**pub.student.h, "Range": bad})
        assert r.status_code == 416, (bad, r.status_code)
        assert r.headers["content-range"] == f"bytes */{size}"
    # Hors vidéo, Range est ignoré : réponse complète (RFC 9110), jamais une plage.
    r = dl.get(pub.url(pub.image), headers={**pub.student.h, "Range": "bytes=0-3"})
    assert r.status_code == 200 and r.content == pub.image_bytes


def test_repeated_ranges_and_byte_budget_are_limited(reader_creds: tuple[str, str], pub: Pub) -> None:
    with delivery_client(reader_creds, media_delivery_range_per_minute_media=5) as c:
        codes = [
            c.get(pub.url(pub.video), headers={**pub.student.h, "Range": f"bytes={i}-{i}"}).status_code
            for i in range(8)
        ]
    assert codes[:5] == [206] * 5 and set(codes[5:]) == {429}
    with delivery_client(reader_creds, media_delivery_bytes_per_minute_user=len(pub.video_bytes) + 10) as c:
        assert c.get(pub.url(pub.video), headers=pub.student.h).status_code == 200
        r = c.get(pub.url(pub.video), headers=pub.student.h)
        assert r.status_code == 429 and "retry-after" in r.headers


# =============================================================================================
# 2–9. Refus
# =============================================================================================
def test_no_session_or_invalid_session_is_401(dl: TestClient, pub: Pub) -> None:
    assert dl.get(pub.url(pub.image)).status_code == 401
    assert dl.get(pub.url(pub.image), headers={"Authorization": "Bearer invalide"}).status_code == 401
    assert dl.get(pub.url(pub.image), headers={"Authorization": "Basic eDp5"}).status_code == 401
    assert dl.get(pub.url(pub.image), cookies={"nl_session": pub.student.token}).status_code == 401
    assert dl.get(f"{D}/{pub.image}/preview").status_code == 401


def test_knowing_the_identifier_is_never_enough(dl: TestClient, w: World, pub: Pub) -> None:
    h = pub.student.h
    # Contexte pédagogique absent, incohérent ou inventé : 404.
    assert dl.get(f"{D}/{pub.image}/content", headers=h).status_code == 404
    assert dl.get(f"{D}/{pub.image}/content?release={pub.release}", headers=h).status_code == 404
    assert dl.get(pub.url(pub.image, release=str(uuid.uuid4())), headers=h).status_code == 404
    assert dl.get(pub.url(pub.image, lv=str(uuid.uuid4())), headers=h).status_code == 404
    assert dl.get(pub.url(pub.image, lv=pub.org_lv), headers=h).status_code == 404
    # Média sain, public, mais rattaché à aucune version de l'édition : 404.
    assert dl.get(pub.url(pub.unattached), headers=h).status_code == 404
    assert dl.get(pub.url(str(uuid.uuid4())), headers=h).status_code == 404
    assert dl.get(f"{D}/pas-un-uuid/content?release=x&lesson_version=y", headers=h).status_code == 404
    # L'apprenant n'a pas d'aperçu éditorial.
    assert dl.get(f"{D}/{pub.image}/preview", headers=h).status_code == 404


def test_cross_establishment_isolation(dl: TestClient, w: World, pub: Pub) -> None:
    # Aperçu éditorial : membres habilités de l'établissement seulement ; ailleurs, existence non révélée.
    assert dl.get(f"{D}/{pub.org_media}/preview", headers=w.org_instr.h).status_code == 200
    assert dl.get(f"{D}/{pub.org_media}/preview", headers=w.other_instr.h).status_code == 404
    assert dl.get(f"{D}/{pub.org_media}/preview", headers=w.org_admin.h).status_code == 404  # aucun droit éditorial
    # Programme d'établissement : jamais exposé par le catalogue, donc jamais diffusé aux apprenants.
    for who in (pub.student, w.org_instr, w.other_instr):
        r = dl.get(pub.url(pub.org_media, lv=pub.org_lv, release=pub.org_release), headers=who.h)
        assert r.status_code == 404, who.email
    # Un média public reste réservé à son contexte public, même pour un auteur d'établissement.
    assert dl.get(f"{D}/{pub.image}/preview", headers=w.other_instr.h).status_code == 404


def test_disabled_user_and_revoked_session_are_refused(
    api: TestClient, dl: TestClient, sync_engine: Engine, pub: Pub
) -> None:
    learner = account(api, sync_engine)
    assert dl.get(pub.url(pub.image), headers=learner.h).status_code == 200
    sql(sync_engine, "UPDATE users SET status = 'suspended' WHERE id = :u", u=learner.user_id)
    assert dl.get(pub.url(pub.image), headers=learner.h).status_code == 401
    sql(sync_engine, "UPDATE users SET status = 'active' WHERE id = :u", u=learner.user_id)
    assert dl.get(pub.url(pub.image), headers=learner.h).status_code == 200
    assert api.post("/api/v1/auth/logout", headers=learner.h).status_code == 204
    assert dl.get(pub.url(pub.image), headers=learner.h).status_code == 401


def test_role_change_is_effective_at_the_next_request(
    api: TestClient, dl: TestClient, sync_engine: Engine, pub: Pub
) -> None:
    learner = account(api, sync_engine)
    assert dl.get(pub.url(pub.image), headers=learner.h).status_code == 200
    sql(sync_engine, "DELETE FROM user_roles WHERE user_id = :u", u=learner.user_id)
    r = dl.get(pub.url(pub.image), headers=learner.h)
    assert r.status_code == 403 and code_of(r) == "forbidden"


def test_infected_and_unscanned_media_are_never_served(
    api: TestClient, dl: TestClient, w: World, worker: Any, sync_engine: Engine
) -> None:
    # EICAR (fichier de test normalisé) déposé en quarantaine de test, analysé par ClamAV : « infected ».
    infected = planted(api, None, sync_engine, eicar(), who=w.author)
    assert worker.process(infected) == "infected"
    pending = uploaded(api, w.author, "image", "image/png", png(with_metadata=False))["id"]  # jamais analysé
    for mid, state in ((infected, "infected"), (pending, "quarantined")):
        assert sql(sync_engine, "SELECT state FROM media_assets WHERE id = :i", i=mid).scalar() == state
        r = dl.get(f"{D}/{mid}/preview", headers=w.author.h)
        assert r.status_code == 404 and b"EICAR" not in r.content
    # Un média non sain ne peut même pas être rattaché à une leçon (donc jamais publié).
    lv = ok(new_version(api, w.author, new_lesson(api, w.author, new_module(api, w, w.author)["id"])["id"]), 201)["id"]
    with pytest.raises(Exception, match="nl:media_not_clean|non rattachable"):
        attach(sync_engine, w.author.user_id, lv, infected)


def test_revoked_media_and_invalidated_verdict(
    api: TestClient, w: World, worker: Any, sync_engine: Engine, reader_creds: tuple[str, str]
) -> None:
    a = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(9, 9)))
    b = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(8, 8)))
    release, lv, _ = publish_with_media(api, w, sync_engine, w.author, [a, b])
    learner = account(api, sync_engine)
    with delivery_client(reader_creds) as c:
        url = lambda m: f"{D}/{m}/content?release={release}&lesson_version={lv}"  # noqa: E731
        assert c.get(url(a), headers=learner.h).status_code == 200
        epoch = sql(sync_engine, "SELECT access_epoch FROM media_assets WHERE id = :i", i=a).scalar()
        # Révocation : réservée aux relecteurs plateforme et à l'administration.
        assert api.post(f"{M}/{a}/revoke", headers=w.author.h, json={"reason": "Essai non autorisé"}).status_code == 403
        r = ok(api.post(f"{M}/{a}/revoke", headers=w.reviewer.h, json={"reason": "Contenu retiré (test)"}))
        assert r["state"] == "revoked"
        assert sql(sync_engine, "SELECT access_epoch FROM media_assets WHERE id = :i", i=a).scalar() == epoch + 1
        assert c.get(url(a), headers=learner.h).status_code == 404
        assert c.get(f"{D}/{a}/preview", headers=w.author.h).status_code == 404
        assert api.post(f"{M}/{a}/revoke", headers=w.reviewer.h, json={"reason": "Deuxième fois"}).status_code == 409
        # La leçon reste publiée (édition figée) ; seul le média devient indisponible.
        assert sql(sync_engine, "SELECT status FROM program_releases WHERE id = :r", r=release).scalar() == "published"
        # Verdict invalidé a posteriori (nouvelle analyse : menace) par l'identité de service.
        assert c.get(url(b), headers=learner.h).status_code == 200
        attempt, sha = sql(sync_engine, "SELECT scan_attempt, sha256 FROM media_assets WHERE id = :i", i=b).one()
        with worker.store.tx() as cur:
            cur.execute(
                "INSERT INTO media_scan_events (media_id, attempt, result, engine, engine_version, signature_version, "
                "signatures_published_at, threat_name, sha256, scanned_by) VALUES (%s, %s, 'infected', 'clamav', "
                "'ClamAV test', '1', now(), 'Test.Fictif', %s, %s)",
                (b, attempt, sha, "00000000-0000-4000-a000-00000000c1a5"),
            )
            cur.execute("UPDATE media_assets SET state = 'infected' WHERE id = %s AND state = 'clean'", (b,))
        assert c.get(url(b), headers=learner.h).status_code == 404
    audit = sql(
        sync_engine, "SELECT metadata::text FROM audit_logs WHERE action = 'media.revoked' AND entity_id = :m", m=a
    ).scalar()
    assert audit and "Contenu retiré" not in audit  # aucun texte libre dans le journal


# =============================================================================================
# 10. Changement d'époque PENDANT un flux long : coupure à la revérification
# =============================================================================================
def test_long_stream_is_cut_when_access_is_revoked(
    api: TestClient,
    w: World,
    worker: Any,
    sync_engine: Engine,
    reader_creds: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = clean_media(api, worker, w.author, "video", "video/mp4", big_mp4(400_000))
    clean_media(api, worker, w.author, "captions", "text/vtt", VTT, captions_for=video)
    release, lv, _ = publish_with_media(api, w, sync_engine, w.author, [video])
    learner = account(api, sync_engine)
    real_open = delivery.CleanReader.open
    revoked: list[bool] = []

    def open_then_revoke(self: Any, grant: Any, start: int, end: int) -> Any:
        body = real_open(self, grant, start, end)

        class Body:
            def iter_chunks(self, size: int) -> Iterator[bytes]:
                for i, chunk in enumerate(body.iter_chunks(size)):
                    if i == 1 and not revoked:  # le relecteur révoque pendant le transfert
                        ok(api.post(f"{M}/{video}/revoke", headers=w.reviewer.h, json={"reason": "Retrait (test)"}))
                        revoked.append(True)
                        time.sleep(0.25)
                    yield chunk

            def close(self) -> None:
                body.close()

        return Body()

    monkeypatch.setattr(delivery.CleanReader, "open", open_then_revoke)
    with delivery_client(reader_creds, media_delivery_recheck_seconds=0.1) as c:
        r = c.get(f"{D}/{video}/content?release={release}&lesson_version={lv}", headers=learner.h)
        announced = int(r.headers["content-length"])
        assert revoked and len(r.content) < announced  # transfert interrompu (octets déjà reçus non repris)
        again = c.get(f"{D}/{video}/content?release={release}&lesson_version={lv}", headers=learner.h)
        assert again.status_code == 404


# =============================================================================================
# 11–13. Stockage : aucun accès direct, identité de lecture seule
# =============================================================================================
def test_no_direct_or_anonymous_storage_access(pub: Pub, sync_engine: Engine) -> None:
    if not REAL_S3:
        pytest.skip("IAM et accès anonyme : RustFS réel uniquement")
    key = sql(sync_engine, "SELECT clean_key FROM media_assets WHERE id = :i", i=pub.image).scalar()
    for url in (f"{S3}/{CLEAN}/{key}", f"{S3}/{CLEAN}/", f"{S3}/{QUARANTINE}/"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(url, timeout=5)  # noqa: S310 - test de non-exposition
        assert exc.value.code == 403


def test_delivery_uses_the_read_only_identity(
    dl: TestClient, pub: Pub, sync_engine: Engine, reader_creds: tuple[str, str]
) -> None:
    s = dl.app.state.settings  # type: ignore[attr-defined]
    assert s.s3_access_key == reader_creds[0] and s.media_clean_bucket == CLEAN
    reader = delivery.CleanReader(s)
    qkey = sql(sync_engine, "SELECT quarantine_key FROM media_assets WHERE id = :i", i=pub.image).scalar()
    ckey = sql(sync_engine, "SELECT clean_key FROM media_assets WHERE id = :i", i=pub.image).scalar()
    assert reader.client.get_object(Bucket=CLEAN, Key=ckey)["Body"].read() == pub.image_bytes
    if REAL_S3:
        from botocore.exceptions import ClientError

        for attempt in (
            lambda: reader.client.get_object(Bucket=QUARANTINE, Key=qkey),
            lambda: reader.client.put_object(Bucket=CLEAN, Key=f"{uuid.uuid4()}/{uuid.uuid4()}", Body=b"x"),
            lambda: reader.client.delete_object(Bucket=CLEAN, Key=ckey),
            lambda: reader.client.list_objects_v2(Bucket=CLEAN),
            lambda: reader.client.create_bucket(Bucket=f"nl-test-intrus-{uuid.uuid4().hex[:8]}"),
        ):
            with pytest.raises(ClientError) as exc:
                attempt()
            assert exc.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
        assert reader.client.get_object(Bucket=CLEAN, Key=ckey)["Body"].read() == pub.image_bytes
    # L'API d'édition (identité de téléversement) ne sert aucun octet ; la diffusion n'écrit rien.
    main_paths = create_app(media_settings()).openapi()["paths"]
    assert not any(p.startswith(D) for p in main_paths)
    assert not any(m != "get" for p, ops in dl.app.openapi()["paths"].items() for m in ops)  # type: ignore[attr-defined]


def test_incoherent_or_missing_clean_object_is_not_served(
    dl: TestClient, api: TestClient, w: World, worker: Any, sync_engine: Engine
) -> None:
    mid = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(7, 5)))
    key = sql(sync_engine, "SELECT clean_key FROM media_assets WHERE id = :i", i=mid).scalar()
    assert dl.get(f"{D}/{mid}/preview", headers=w.author.h).status_code == 200
    s3_client().put_object(Bucket=CLEAN, Key=key, Body=b"remplace", Metadata={"sha256": "0" * 64})  # altération
    r = dl.get(f"{D}/{mid}/preview", headers=w.author.h)
    assert r.status_code == 503 and code_of(r) == "media_integrity"
    s3_client().delete_object(Bucket=CLEAN, Key=key)
    r = dl.get(f"{D}/{mid}/preview", headers=w.author.h)
    assert r.status_code == 503 and b"nl-media" not in r.content


# =============================================================================================
# 17–19. Pannes et limitation de débit
# =============================================================================================
def test_storage_failure_before_and_during_a_stream(
    reader_creds: tuple[str, str], pub: Pub, monkeypatch: pytest.MonkeyPatch
) -> None:
    with delivery_client(reader_creds, s3_endpoint="http://127.0.0.1:9") as c:
        r = c.get(pub.url(pub.image), headers=pub.student.h)
        assert r.status_code == 503 and code_of(r) == "media_unavailable"
    real_open = delivery.CleanReader.open

    def broken(self: Any, grant: Any, start: int, end: int) -> Any:
        body = real_open(self, grant, start, end)

        class Body:
            def iter_chunks(self, size: int) -> Iterator[bytes]:
                it = body.iter_chunks(size)
                yield next(it)
                raise ConnectionResetError("stockage coupé (test)")

            def close(self) -> None:
                body.close()

        return Body()

    monkeypatch.setattr(delivery.CleanReader, "open", broken)
    with delivery_client(reader_creds) as c:
        r = c.get(pub.url(pub.video), headers=pub.student.h)
        assert len(r.content) < int(r.headers["content-length"])  # coupure visible, jamais un faux succès complet
    monkeypatch.undo()
    with delivery_client(reader_creds, media_delivery_concurrent_streams_user=1) as c:  # compteur libéré
        assert c.get(pub.url(pub.image), headers=pub.student.h).status_code == 200
        assert c.get(pub.url(pub.image), headers=pub.student.h).status_code == 200


def test_database_failure_closes_delivery(reader_creds: tuple[str, str], pub: Pub) -> None:
    with delivery_client(reader_creds, database_url="postgresql+asyncpg://nobody:x@127.0.0.1:9/none") as c:
        r = c.get(pub.url(pub.image), headers=pub.student.h)
        assert r.status_code == 503 and code_of(r) == "media_unavailable"
        assert c.get("/ready").status_code == 503


def test_rate_limits_per_user_endpoint_and_establishment(reader_creds: tuple[str, str], pub: Pub, w: World) -> None:
    with delivery_client(reader_creds, media_delivery_content_per_minute_user=3) as c:
        codes = [c.get(pub.url(pub.image), headers=pub.student.h).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        r = c.get(pub.url(pub.image), headers=pub.student.h)
        assert int(r.headers["retry-after"]) >= 0 and code_of(r) == "rate_limited"
        # Limite par point d'accès : l'aperçu a son propre compteur.
        assert c.get(f"{D}/{pub.org_media}/preview", headers=w.org_instr.h).status_code == 200
    with delivery_client(reader_creds, media_delivery_per_minute_org=2) as c:
        assert [c.get(f"{D}/{pub.org_media}/preview", headers=w.org_instr.h).status_code for _ in range(3)] == [
            200,
            200,
            429,
        ]
    with delivery_client(reader_creds, redis_url="redis://127.0.0.1:9/0") as c:  # fermé par défaut
        r = c.get(pub.url(pub.image), headers=pub.student.h)
        assert r.status_code == 503


def test_concurrent_streams_are_limited(reader_creds: tuple[str, str], pub: Pub) -> None:
    with delivery_client(reader_creds, media_delivery_concurrent_streams_user=2) as c:
        redis = None
        import redis as redis_lib

        redis = redis_lib.Redis.from_url(REDIS or "")
        key = f"nl:md:{delivery._ns(c.app.state.settings)}:cc:u:{pub.student.user_id}"  # type: ignore[attr-defined]
        redis.set(key, 2, ex=60)  # deux flux déjà ouverts
        r = c.get(pub.url(pub.image), headers=pub.student.h)
        assert r.status_code == 429 and code_of(r) == "too_many_streams"
        redis.delete(key)
        assert c.get(pub.url(pub.image), headers=pub.student.h).status_code == 200
        assert int(redis.get(key) or 0) == 0


# =============================================================================================
# 20–21. Conservation : obligation légale, éditions figées
# =============================================================================================
def test_legal_hold_blocks_deletion_and_never_grants_access(
    api: TestClient, w: World, worker: Any, sync_engine: Engine, dl: TestClient
) -> None:
    admin = account(api, sync_engine, ("admin", None))
    mid = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(6, 6)))
    url = os.environ.get("NL_TEST_ADMIN_DATABASE_URL") or sync_engine.url.render_as_string(hide_password=False)
    assert media_legal_hold(mid, w.author.email, "Enquête fictive (test)", False, url=url) == 1  # pas admin
    assert media_legal_hold(mid, admin.email, None, False, url=url) == 2
    assert media_legal_hold(mid, admin.email, "Enquête fictive (test)", False, url=url) == 0
    assert sql(sync_engine, "SELECT legal_hold FROM media_assets WHERE id = :i", i=mid).scalar() is True
    r = api.delete(f"{M}/{mid}", headers=w.author.h)
    assert r.status_code in (409, 422) and "legal_hold" in r.text
    # La conservation n'ouvre aucun accès : toujours hors édition, donc jamais diffusé aux apprenants.
    assert dl.get(f"{D}/{mid}/preview", headers=w.student.h).status_code == 404
    # L'API (nl_api) ne peut pas poser elle-même une conservation légale.
    api_url = os.environ.get("DATABASE_URL", "")
    if "nl_api" in api_url:
        eng = create_engine(api_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1))
        with pytest.raises(Exception, match="(?i)permission|privil"), eng.begin() as c:
            c.execute(
                text("UPDATE media_assets SET legal_hold = false, legal_hold_reason = NULL WHERE id = :i"), {"i": mid}
            )
        eng.dispose()
    assert media_legal_hold(mid, admin.email, None, True, url=url) == 0
    assert (
        sql(
            sync_engine,
            "SELECT count(*) FROM audit_logs WHERE entity_id = :m AND action LIKE 'media.legal_hold%'",
            m=mid,
        ).scalar()
        == 2
    )
    assert api.delete(f"{M}/{mid}", headers=w.author.h).status_code == 204


def test_frozen_editions_protect_their_media(api: TestClient, w: World, pub: Pub, sync_engine: Engine) -> None:
    # Détacher un média d'une version publiée : refusé par la base.
    with pytest.raises(Exception, match="nl:not_draft|protégée"), sync_engine.begin() as c:
        c.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": w.author.user_id})
        c.execute(
            text("DELETE FROM lesson_version_media WHERE lesson_version_id = :l AND media_id = :m"),
            {"l": pub.lv, "m": pub.image},
        )
    # Supprimer un média rattaché : refusé par l'API (et la base).
    r = api.delete(f"{M}/{pub.image}", headers=w.author.h)
    assert r.status_code in (409, 422) and "in_use" in r.text
    assert sql(sync_engine, "SELECT count(*) FROM lesson_version_media WHERE media_id = :m", m=pub.image).scalar() == 1


def test_retention_report_is_read_only(sync_engine: Engine) -> None:
    import psycopg

    from app.ops.media_retention import report

    url = os.environ.get("NL_TEST_ADMIN_DATABASE_URL") or sync_engine.url.render_as_string(hide_password=False)
    url = url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
    before = sql(sync_engine, "SELECT count(*), count(*) FILTER (WHERE purged_at IS NOT NULL) FROM media_assets").one()
    with psycopg.connect(url) as conn:
        conn.read_only = True
        out = report(conn)
    assert out["purge_automatique"].startswith("desactivee")
    assert (
        sql(sync_engine, "SELECT count(*), count(*) FILTER (WHERE purged_at IS NOT NULL) FROM media_assets").one()
        == before
    )


# =============================================================================================
# 22–24. Aucune fuite, aucune publication médicale, aucune régression D14/R3
# =============================================================================================
def test_no_medical_publication_and_application_identities(dl: TestClient, sync_engine: Engine) -> None:
    assert sql(sync_engine, "SELECT count(*) FROM programs WHERE is_medical AND status = 'published'").scalar() == 0
    assert dl.app.state.settings.medical_publication_enabled is False  # type: ignore[attr-defined]
    url = os.environ.get("DATABASE_URL", "")
    if "nl_api" in url:  # D14 : la diffusion se connecte avec le rôle applicatif, sans privilège
        eng = create_engine(url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1))
        with eng.connect() as c:
            assert c.execute(text("SELECT current_user")).scalar() == "nl_api"
            assert c.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar() is False
        eng.dispose()

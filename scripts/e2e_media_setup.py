"""Préparation des parcours Playwright des médias (2.2.3-e, E3). Données ENTIÈREMENT FICTIVES.

Exécuté par scripts/validate-phase2.2.3e.ps1 (-Stage e3) dans un conteneur python:3.12-slim relié au
réseau Docker de la pile (jamais en production). Crée :
  - deux établissements fictifs (A, B) et sept comptes `@example.test`, STABLES d'un passage à l'autre
    (inscrits une seule fois) ; mot de passe aléatoire renouvelé à chaque passage, jamais affiché ni
    journalisé, transmis à Playwright par variable d'environnement ;
  - un module public avec une leçon en BROUILLON et une leçon SOUMISE (version figée) ; un module de
    l'établissement B avec un brouillon ;
  - un média de l'établissement B (refus inter-établissements) ;
  - un média « infecté » : chaîne de test EICAR construite EN MÉMOIRE, déposée telle quelle en quarantaine
    avec l'identité de TÉLÉVERSEMENT (aucun format ne l'accepterait : c'est le cas adverse des tests 2.2.3-c),
    puis analysée par le VRAI ClamAV (media-worker). Elle n'est jamais écrite sur disque ;
  - les fichiers de test (PNG, JPEG, WebP, PDF, MP4 H.264 réel de 40 s généré par ffmpeg, WebVTT).
Sortie : une ligne JSON (identifiants et comptes) sur la sortie standard. Aucune suppression.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3
import psycopg
import redis
from botocore.config import Config
from PIL import Image
from pypdf import PdfWriter

API = os.environ["API_URL"].rstrip("/")
ADMIN = os.environ["ADMIN_DSN"]
FIXTURES = Path(os.environ.get("FIXTURES_DIR", "/fixtures"))
TAG = uuid.uuid4().hex[:6]


def call(method: str, path: str, token: str | None = None, body: object = None, raw: bytes | None = None) -> tuple:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if raw is not None:
        req.add_header("Content-Type", "application/octet-stream")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode() or "{}"
            return r.status, json.loads(text)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def must(result: tuple, *codes: int) -> dict:
    status, data = result
    if status not in codes:
        raise SystemExit(f"Échec de préparation : HTTP {status} {data.get('type', '')} {data.get('code', '')}")
    return data


def org(name: str) -> str:
    """Établissement fictif STABLE (réutilisé d'un passage à l'autre : aucune accumulation)."""
    slug = f"e2e-medias-{name.lower()}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        row = c.execute("SELECT id FROM organizations WHERE slug = %s", (slug,)).fetchone()
        if row:
            return str(row[0])
        return str(
            c.execute(
                "INSERT INTO organizations (slug, name, kind) VALUES (%s, %s, 'school') RETURNING id",
                (slug, f"Établissement fictif E2E {name}"),
            ).fetchone()[0]
        )


def account(tag: str, role: str, organization: str | None = None) -> dict:
    """Compte fictif STABLE : inscrit par l'API au premier passage seulement (la limite d'inscriptions
    par adresse IP reste active) ; à chaque passage, nouveau mot de passe aléatoire posé avec le hachage
    Argon2id de l'application, verrouillage levé, rôle assuré. Mot de passe jamais affiché."""
    from app.core.security import hash_password

    email = f"e2e-medias-{tag}@example.test"
    password = f"E2e-{secrets.token_urlsafe(18)}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        exists = c.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone()
    if not exists:
        must(
            call(
                "POST",
                "/api/v1/auth/register",
                body={
                    "email": email,
                    "password": password,
                    "display_name": f"Compte fictif {tag}",
                    "locale": "fr",
                    "adult_attestation": True,
                    "accept_terms": True,
                    "accept_privacy": True,
                },
            ),
            202,
        )
    with psycopg.connect(ADMIN, autocommit=True) as c:
        # Compte fictif de test : vérification du courriel simulée (aucun courriel réel).
        c.execute(
            "UPDATE users SET status = 'active', email_verified_at = coalesce(email_verified_at, now()), "
            "password_hash = %s, failed_login_count = 0, locked_until = NULL WHERE email = %s",
            (hash_password(password), email),
        )
        user_id = str(c.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0])
        has_role = c.execute(
            "SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = %s AND r.code = %s "
            "AND ur.organization_id IS NOT DISTINCT FROM %s",
            (user_id, role, organization),
        ).fetchone()
        if not has_role:
            with c.transaction():
                c.execute("SELECT set_config('nl.role_grant_bypass', 'on', true)")
                c.execute(
                    "INSERT INTO user_roles (user_id, role_id, organization_id) SELECT %s, id, %s FROM roles WHERE code = %s",
                    (user_id, organization, role),
                )
    token = must(call("POST", "/api/v1/auth/login", body={"email": email, "password": password}), 200)["session_token"]
    return {"email": email, "password": password, "id": user_id, "_token": token}


def png(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(300, 300)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def fixtures() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "schema-fictif.png").write_bytes(png((480, 300), (30, 120, 200)))
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (200, 80, 40)).save(buf, "JPEG", quality=85)
    (FIXTURES / "photo-fictive.jpg").write_bytes(buf.getvalue())
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), (40, 160, 90)).save(buf, "WEBP")
    (FIXTURES / "illustration-fictive.webp").write_bytes(buf.getvalue())
    (FIXTURES / "fiche-fictive.pdf").write_bytes(pdf())
    (FIXTURES / "sous-titres-fictifs.vtt").write_bytes(
        "WEBVTT\n\n00:00.000 --> 00:05.000\nSous-titre fictif de démonstration\n\n"
        "00:05.000 --> 00:40.000\nSuite fictive\n".encode()
    )
    import imageio_ffmpeg

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:duration=40",
            "-vf",
            "noise=alls=40:allf=t",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-b:v",
            "4M",
            "-maxrate",
            "4M",
            "-bufsize",
            "8M",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(FIXTURES / "video-fictive.mp4"),
        ],
        check=True,
    )


def upload(token: str, kind: str, mime: str, data: bytes, name: str, **extra: object) -> str:
    media = must(
        call(
            "POST",
            "/api/v1/editorial/media",
            token,
            {
                "kind": kind,
                "mime_type": mime,
                "size_bytes": len(data),
                "display_name": name,
                "license": "CC-BY-4.0",
                **extra,
            },
        ),
        201,
    )
    must(call("PUT", f"/api/v1/editorial/media/{media['id']}/content", token, raw=data), 200)
    return str(media["id"])


def lesson(token: str, specialty: str, title: str, organization: str | None = None) -> tuple[str, str]:
    body: dict = {
        "code": f"e2e_{uuid.uuid4().hex[:8]}",
        "specialty_id": specialty,
        "level": "beginner",
        "title": {"fr": f"Module fictif E2E — {title}", "en": f"Fictitious E2E module — {title}"},
    }
    if organization:
        body |= {"organization_id": organization, "access_tier": "organization"}
    module = must(call("POST", "/api/v1/editorial/modules", token, body), 201)
    les = must(
        call(
            "POST",
            f"/api/v1/editorial/modules/{module['id']}/lessons",
            token,
            {"code": f"e2e_{uuid.uuid4().hex[:8]}", "position": 0},
        ),
        201,
    )
    version = must(
        call(
            "POST",
            f"/api/v1/editorial/lessons/{les['id']}/versions",
            token,
            {
                "locale": "fr",
                "title": f"Leçon fictive E2E — {title}",
                "body": {
                    "schema": "blocks/v1",
                    "blocks": [{"id": "p1", "type": "paragraph", "text": "Contenu fictif non médical."}],
                },
            },
        ),
        201,
    )
    return str(module["id"]), str(version["id"])


def planted_infected(token: str, user_id: str) -> str:
    """EICAR en mémoire → quarantaine (identité de téléversement), réception déclarée, mise en file."""
    eicar = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
    media = must(
        call(
            "POST",
            "/api/v1/editorial/media",
            token,
            {
                "kind": "document",
                "mime_type": "application/pdf",
                "size_bytes": len(eicar),
                "display_name": f"Fichier de test antivirus fictif {TAG}.pdf",
                "license": "CC-BY-4.0",
            },
        ),
        201,
    )
    key = f"{uuid.uuid4()}/{uuid.uuid4()}"
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_UPLOADER_KEY"],
        aws_secret_access_key=os.environ["S3_UPLOADER_SECRET"],
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    s3.put_object(Bucket=os.environ["QUARANTINE_BUCKET"], Key=key, Body=eicar, ContentType="application/octet-stream")
    with psycopg.connect(ADMIN, autocommit=True) as c, c.transaction():
        c.execute("SELECT set_config('nl.actor_id', %s, true)", (user_id,))
        c.execute(
            "UPDATE media_assets SET state = 'quarantined', byte_size = %s, sha256 = %s, quarantine_key = %s WHERE id = %s",
            (len(eicar), hashlib.sha256(eicar).hexdigest(), key, media["id"]),
        )
    r = redis.Redis.from_url(os.environ["REDIS_URL"])
    r.xadd(
        os.environ.get("MEDIA_SCAN_STREAM", "nl:media:scan"),
        {"media_id": str(media["id"])},
        maxlen=10_000,
        approximate=True,
    )
    return str(media["id"])


def main() -> int:
    fixtures()
    org_a, org_b = org("A"), org("B")
    accounts = {
        "author": account("auteur", "instructor"),
        "author2": account("auteur2", "instructor"),
        "quota": account("quota", "instructor"),
        "reviewer": account("relecteur", "medical_reviewer"),
        "admin": account("admin", "admin"),
        "orgA": account("etab-a", "instructor", org_a),
        "orgB": account("etab-b", "instructor", org_b),
    }
    with psycopg.connect(ADMIN) as c:
        specialty = str(c.execute("SELECT id FROM specialties ORDER BY code LIMIT 1").fetchone()[0])
    author = accounts["author"]["_token"]
    module, draft = lesson(author, specialty, "brouillon")
    _, frozen = lesson(author, specialty, "version soumise")
    must(call("POST", f"/api/v1/editorial/lesson-versions/{frozen}/submit", author), 200)
    _, org_b_draft = lesson(accounts["orgB"]["_token"], specialty, "établissement B", org_b)
    org_b_media = upload(
        accounts["orgB"]["_token"],
        "image",
        "image/png",
        png((60, 40), (120, 60, 160)),
        f"Image fictive établissement B {TAG}.png",
        organization_id=org_b,
        alt_text={"fr": "Image fictive B"},
    )
    infected = planted_infected(author, accounts["author"]["id"])
    for a in accounts.values():
        a.pop("_token")
    print(
        json.dumps(
            {
                "tag": TAG,
                "accounts": accounts,
                "orgA": org_a,
                "orgB": org_b,
                "module": module,
                "draftVersion": draft,
                "frozenVersion": frozen,
                "orgBDraftVersion": org_b_draft,
                "orgBMedia": org_b_media,
                "infectedMedia": infected,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Sauvegarde chiffrée et restauration ISOLÉE des médias protégés (2.2.3-e, D22).

Exécuté dans l'image locale `nurselearn-backup-tools` (infra/backup/Dockerfile) par
scripts/backup-restore-media.ps1. Trois commandes :

  backup  : base PostgreSQL (pg_dump, format personnalisé), objets de QUARANTAINE et SAINS, métadonnées
            des médias, inventaire du stockage et informations de restauration, réunis dans une archive
            tar CHIFFRÉE au fil de l'eau (AES-256-GCM par blocs, ordre et fin authentifiés). Aucun clair
            n'est écrit hors du tmpfs du conteneur. Manifeste public (sans secret) à côté de l'archive.
            Lecture du stockage avec les identités R3 de LECTURE : quarantaine (nl-media-scanner, lecture
            et liste) et sain (nl-media-reader). La clé racine RustFS n'est jamais utilisée.
  verify  : empreinte de l'archive (manifeste), déchiffrement complet et authentifié ; détection
            d'altération (--tamper) et de mauvaise clé (--wrong-key) : les deux DOIVENT échouer.
  restore : restauration dans un environnement ISOLÉ (PostgreSQL et RustFS jetables, réseau interne,
            stockage en mémoire), jamais dans la base de travail ; puis vérification : intégrité, tables,
            rattachements, empreintes des objets, états antivirus, verdicts, permissions (D14).

Clé : BACKUP_ENCRYPTION_KEY (64 caractères hexadécimaux, .env local, jamais affichée ni archivée).
Sortie : une ligne JSON par commande (aucun secret).
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import secrets
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

FORMAT = 1
MAGIC = b"NLBK"
CHUNK = 4 * 1048576
TABLES = [
    "users",
    "organizations",
    "user_roles",
    "programs",
    "modules",
    "lessons",
    "lesson_versions",
    "program_releases",
    "media_assets",
    "media_scan_events",
    "lesson_version_media",
    "media_legal_hold_events",
    "media_usage",
    "editorial_decisions",
    "audit_logs",
]
MEDIA_COLUMNS = (
    "id, organization_id, kind, mime_type, state, byte_size, sha256, quarantine_key, clean_key, access_epoch, "
    "legal_hold, deleted_at IS NOT NULL AS deleted, purged_at IS NOT NULL AS purged"
)


# =============================================================================================
# Chiffrement par blocs (AES-256-GCM) : en-tête, puis [longueur][bloc chiffré + étiquette] … ;
# nonce = préfixe aléatoire (8 o) + numéro de bloc (4 o) ; données associées = en-tête, numéro, fin.
# Un bloc retiré, déplacé, modifié ou une fin tronquée sont détectés.
# =============================================================================================
def _key(hex_key: str) -> bytes:
    key = bytes.fromhex(hex_key.strip())
    if len(key) != 32:
        raise SystemExit("BACKUP_ENCRYPTION_KEY : 32 octets (64 caractères hexadécimaux) attendus")
    return key


def fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"nl-backup-key:" + key).hexdigest()[:16]


class Encryptor(io.RawIOBase):
    """Flux d'écriture : chiffre par blocs de 4 Mio et calcule l'empreinte du fichier chiffré."""

    def __init__(self, out: BinaryIO, key: bytes) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.out, self.aead, self.prefix = out, AESGCM(key), secrets.token_bytes(8)
        self.header = MAGIC + bytes([FORMAT]) + self.prefix
        self.buf, self.index, self.size = bytearray(), 0, 0
        self.digest = hashlib.sha256()
        self._emit(self.header)

    def _emit(self, data: bytes) -> None:
        self.out.write(data)
        self.digest.update(data)
        self.size += len(data)

    def _block(self, data: bytes, final: bool) -> None:
        nonce = self.prefix + struct.pack(">I", self.index)
        aad = self.header + struct.pack(">I?", self.index, final)
        sealed = self.aead.encrypt(nonce, data, aad)
        self._emit(struct.pack(">I", len(sealed)) + sealed)
        self.index += 1

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        self.buf.extend(data)
        while len(self.buf) > CHUNK:
            self._block(bytes(self.buf[:CHUNK]), False)
            del self.buf[:CHUNK]
        return len(data)

    def finish(self) -> tuple[str, int]:
        self._block(bytes(self.buf), True)
        self.buf.clear()
        return self.digest.hexdigest(), self.size


def decrypt_blocks(src: BinaryIO, key: bytes, tamper_at: int | None = None) -> Iterator[bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aead = AESGCM(key)
    header = src.read(len(MAGIC) + 1 + 8)
    if header[:4] != MAGIC or header[4] != FORMAT:
        raise ValueError("archive non reconnue")
    prefix, index, position = header[5:], 0, len(header)
    while True:
        raw = src.read(4)
        if len(raw) != 4:
            raise ValueError("archive tronquée (fin authentifiée absente)")
        (length,) = struct.unpack(">I", raw)
        sealed = bytearray(src.read(length))
        if len(sealed) != length:
            raise ValueError("archive tronquée")
        if tamper_at is not None and position <= tamper_at < position + 4 + length:
            sealed[max(0, tamper_at - position - 4) % length] ^= 0x01  # altération simulée (test)
        position += 4 + length
        nonce = prefix + struct.pack(">I", index)
        for final in (False, True):
            try:
                plain = aead.decrypt(nonce, bytes(sealed), header + struct.pack(">I?", index, final))
                break
            except Exception:
                plain = None
        if plain is None:
            raise ValueError("authentification du bloc impossible (clé erronée ou archive altérée)")
        yield plain
        index += 1
        if final:
            if src.read(1):
                raise ValueError("données après la fin authentifiée")
            return


class BlockReader(io.RawIOBase):
    def __init__(self, blocks: Iterator[bytes]) -> None:
        self.blocks, self.pending = blocks, b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        while not self.pending:
            try:
                self.pending = next(self.blocks)
            except StopIteration:
                return 0
        n = min(len(b), len(self.pending))
        b[:n] = self.pending[:n]
        self.pending = self.pending[n:]
        return n


# =============================================================================================
# Outils
# =============================================================================================
def s3(endpoint: str, key: str, secret: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


def pg_env(prefix: str) -> dict[str, str]:
    return {
        "PGHOST": os.environ[f"{prefix}_HOST"],
        "PGPORT": os.environ.get(f"{prefix}_PORT", "5432"),
        "PGUSER": os.environ[f"{prefix}_USER"],
        "PGPASSWORD": os.environ[f"{prefix}_PASSWORD"],
        "PGDATABASE": os.environ[f"{prefix}_DB"],
    }


def connect(prefix: str, readonly: bool = False) -> Any:
    import psycopg

    e = pg_env(prefix)
    conn = psycopg.connect(
        host=e["PGHOST"],
        port=e["PGPORT"],
        user=e["PGUSER"],
        password=e["PGPASSWORD"],
        dbname=e["PGDATABASE"],
        autocommit=True,
    )
    if readonly:
        conn.execute("SET default_transaction_read_only = on")
    return conn


def counts(conn: Any) -> dict[str, int]:
    return {t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in TABLES}  # noqa: S608 - liste fixe


def media_rows(conn: Any) -> list[dict[str, Any]]:
    cur = conn.execute(f"SELECT {MEDIA_COLUMNS} FROM media_assets ORDER BY id")  # noqa: S608 - colonnes fixes
    names = [d.name for d in cur.description]
    return [
        {k: (str(v) if k in ("id", "organization_id") and v is not None else v) for k, v in zip(names, r, strict=True)}
        for r in cur.fetchall()
    ]


def add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
    tar.addfile(info, io.BytesIO(data))


def emit(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, default=str))


# =============================================================================================
# backup
# =============================================================================================
def backup(args: argparse.Namespace) -> int:
    key = _key(os.environ["BACKUP_ENCRYPTION_KEY"])
    stamp = args.stamp or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    archive, manifest_path = out / f"nurselearn-media-{stamp}.nlbk", out / f"nurselearn-media-{stamp}.manifest.json"
    work = Path(tempfile.mkdtemp(dir="/work"))
    env = {**os.environ, **pg_env("SRC")}
    # 1. Base : instantané cohérent (pg_dump, une transaction) puis rôles sans mot de passe.
    dump = work / "database.dump"
    # --create : CREATE DATABASE et droits de la base (REVOKE TEMPORARY, CONNECT des rôles applicatifs, D14).
    subprocess.run(["pg_dump", "-Fc", "--create", "--no-subscriptions", "-f", str(dump)], env=env, check=True)
    roles = subprocess.run(
        ["pg_dumpall", "--roles-only", "--no-role-passwords"], env=env, check=True, capture_output=True
    ).stdout
    with connect("SRC", readonly=True) as conn:
        table_counts = counts(conn)
        media = media_rows(conn)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        states: dict[str, int] = {}
        for m in media:
            states[m["state"]] = states.get(m["state"], 0) + 1
    endpoint = os.environ["S3_ENDPOINT"]
    quarantine = s3(endpoint, "nl-media-scanner", os.environ["S3_SCANNER_SECRET_KEY"])
    clean = s3(endpoint, "nl-media-reader", os.environ["S3_READER_SECRET_KEY"])
    buckets = {
        "quarantine": os.environ.get("MEDIA_QUARANTINE_BUCKET", "nl-media-quarantine"),
        "clean": os.environ.get("MEDIA_CLEAN_BUCKET", "nl-media-clean"),
    }
    # Inventaire réel du stockage (liste : identité d'analyse, lecture seule utilisée ici).
    listed: dict[str, list[dict[str, Any]]] = {}
    for kind, bucket in buckets.items():
        listed[kind] = []
        for page in quarantine.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            listed[kind] += [{"key": o["Key"], "size": o["Size"]} for o in page.get("Contents", [])]
    objects: list[dict[str, Any]] = []
    with open(archive, "wb") as raw:
        enc = Encryptor(raw, key)
        with tarfile.open(fileobj=enc, mode="w|") as tar:
            tar.add(dump, arcname="database/database.dump")
            add_bytes(tar, "database/roles.sql.gz", gzip.compress(roles))
            for kind, client in (("quarantine", quarantine), ("clean", clean)):
                for item in listed[kind]:
                    obj = client.get_object(Bucket=buckets[kind], Key=item["key"])
                    body = obj["Body"].read()
                    digest = hashlib.sha256(body).hexdigest()
                    objects.append(
                        {
                            "bucket": kind,
                            "key": item["key"],
                            "size": len(body),
                            "sha256": digest,
                            "metadata_sha256": obj.get("Metadata", {}).get("sha256"),
                        }
                    )
                    add_bytes(tar, f"objects/{kind}/{item['key']}", body)
            detail = {
                "format": FORMAT,
                "created": stamp,
                "alembic_revision": revision,
                "counts": table_counts,
                "media": media,
                "objects": objects,
                "buckets": buckets,
                "inventory": args.inventory,
                "restore": "voir docs/GUIDE-SAUVEGARDE-RESTAURATION-MEDIAS.md",
            }
            add_bytes(tar, "manifest-detail.json", json.dumps(detail, default=str, indent=1).encode())
        digest, size = enc.finish()
    dump.unlink()
    manifest = {
        "format": FORMAT,
        "created": stamp,
        "archive": archive.name,
        "archive_sha256": digest,
        "archive_bytes": size,
        "cipher": "AES-256-GCM (blocs de 4 Mio, fin authentifiée)",
        "key_fingerprint": fingerprint(key),
        "alembic_revision": revision,
        "database": env["PGDATABASE"],
        "counts": table_counts,
        "media_states": states,
        "objects": {k: sum(1 for o in objects if o["bucket"] == k) for k in buckets},
        "object_bytes": sum(o["size"] for o in objects),
        "objects_without_media": [
            o["key"]
            for o in objects
            if not any(m["quarantine_key"] == o["key"] or m["clean_key"] == o["key"] for m in media)
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    emit(
        {
            "ok": True,
            "manifest": manifest_path.name,
            **{
                k: manifest[k]
                for k in ("archive", "archive_bytes", "alembic_revision", "objects", "object_bytes", "media_states")
            },
            "media": table_counts["media_assets"],
            "attachments": table_counts["lesson_version_media"],
        }
    )
    return 0


# =============================================================================================
# verify
# =============================================================================================
def verify(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text())
    archive = Path(args.manifest).with_name(manifest["archive"])
    key = secrets.token_bytes(32) if args.wrong_key else _key(os.environ["BACKUP_ENCRYPTION_KEY"])
    digest = hashlib.sha256()
    with open(archive, "rb") as f:
        for part in iter(lambda: f.read(CHUNK), b""):
            digest.update(part)
    hash_ok = digest.hexdigest() == manifest["archive_sha256"]
    tamper = archive.stat().st_size // 2 if args.tamper else None
    try:
        names, total = 0, 0
        with (
            open(archive, "rb") as f,
            tarfile.open(fileobj=BlockReader(decrypt_blocks(f, key, tamper)), mode="r|") as tar,
        ):
            for member in tar:
                names += 1
                total += member.size
                if member.isfile():
                    tar.extractfile(member).read()  # type: ignore[union-attr]
        decrypted = True
        error = None
    except Exception as exc:  # altération ou mauvaise clé : échec attendu et signalé
        decrypted, error = False, type(exc).__name__ + ": " + str(exc)[:120]
    emit(
        {
            "ok": hash_ok and decrypted,
            "hash_ok": hash_ok,
            "decrypted": decrypted,
            "members": names if decrypted else 0,
            "key_fingerprint_ok": fingerprint(key) == manifest["key_fingerprint"],
            "tamper": bool(args.tamper),
            "wrong_key": bool(args.wrong_key),
            "error": error,
        }
    )
    return 0


# =============================================================================================
# restore (environnement ISOLÉ)
# =============================================================================================
def restore(args: argparse.Namespace) -> int:
    key = _key(os.environ["BACKUP_ENCRYPTION_KEY"])
    manifest = json.loads(Path(args.manifest).read_text())
    archive = Path(args.manifest).with_name(manifest["archive"])
    dst_env = {**os.environ, **pg_env("DST")}
    if os.environ.get("SRC_HOST"):
        raise SystemExit("Restauration refusée : aucune connexion à la base de travail n'est admise ici")
    target = s3(os.environ["RESTORE_S3_ENDPOINT"], os.environ["RESTORE_S3_KEY"], os.environ["RESTORE_S3_SECRET"])
    for _ in range(60):
        try:
            target.list_buckets()
            break
        except Exception:
            time.sleep(2)
    buckets = {"quarantine": "restore-quarantine", "clean": "restore-clean"}
    for b in buckets.values():
        target.create_bucket(Bucket=b)
    work = Path(tempfile.mkdtemp(dir="/work"))
    detail: dict[str, Any] = {}
    restored_objects: dict[tuple[str, str], tuple[int, str, str | None]] = {}
    with open(archive, "rb") as f, tarfile.open(fileobj=BlockReader(decrypt_blocks(f, key)), mode="r|") as tar:
        for member in tar:
            data = tar.extractfile(member).read() if member.isfile() else b""  # type: ignore[union-attr]
            if member.name == "database/database.dump":
                (work / "database.dump").write_bytes(data)
            elif member.name == "database/roles.sql.gz":
                (work / "roles.sql").write_bytes(gzip.decompress(data))
            elif member.name == "manifest-detail.json":
                detail = json.loads(data)
            elif member.name.startswith("objects/"):
                _, kind, object_key = member.name.split("/", 2)
                restored_objects[(kind, object_key)] = (len(data), hashlib.sha256(data).hexdigest(), None)
                target.put_object(Bucket=buckets[kind], Key=object_key, Body=data)
    # Rôles (sans mot de passe : aucune connexion possible sans action explicite), puis la base.
    subprocess.run(
        ["psql", "-X", "-q", "-d", "postgres", "-f", str(work / "roles.sql")],
        env=dst_env,
        check=False,
        capture_output=True,
    )
    # La base est recréée sous son nom d'origine DANS LE SERVEUR ISOLÉ (--create : droits de la base
    # compris). Le serveur de travail n'est jamais joignable depuis ce conteneur (réseau interne).
    os.environ["DST_DB"] = manifest["database"]
    dst_env = {**os.environ, **pg_env("DST")}
    restored = subprocess.run(
        ["pg_restore", "--create", "--exit-on-error", "-d", "postgres", str(work / "database.dump")],
        env=dst_env,
        capture_output=True,
        text=True,
    )
    (work / "database.dump").unlink()
    checks: dict[str, Any] = {"pg_restore": restored.returncode == 0}
    if restored.returncode != 0:
        checks["pg_restore_error"] = restored.stderr.strip().splitlines()[-1:]
    with connect("DST", readonly=True) as conn:
        c = counts(conn)
        checks["tables_identiques"] = c == manifest["counts"]
        if not checks["tables_identiques"]:
            checks["ecarts_tables"] = {
                t: (manifest["counts"].get(t), c[t]) for t in c if c[t] != manifest["counts"].get(t)
            }
        checks["revision"] = (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == manifest["alembic_revision"]
        )
        media = {m["id"]: m for m in media_rows(conn)}
        states: dict[str, int] = {}
        for m in media.values():
            states[m["state"]] = states.get(m["state"], 0) + 1
        checks["etats_antivirus_identiques"] = states == manifest["media_states"]
        # Chaque média sain : dernier verdict traçable « clean » de la tentative courante.
        untraced = conn.execute(
            "SELECT count(*) FROM media_assets m WHERE m.state = 'clean' AND NOT EXISTS (SELECT 1 FROM media_scan_events e "
            "WHERE e.media_id = m.id AND e.result = 'clean' AND e.sha256 = m.sha256)"
        ).fetchone()[0]
        checks["verdicts_sains_tracables"] = untraced == 0
        orphans = conn.execute(
            "SELECT count(*) FROM lesson_version_media l LEFT JOIN media_assets m ON m.id = l.media_id WHERE m.id IS NULL"
        ).fetchone()[0]
        checks["rattachements_coherents"] = (
            orphans == 0 and c["lesson_version_media"] == manifest["counts"]["lesson_version_media"]
        )
        # Objets : présence et empreintes conformes à la base (octets réels, pas seulement la métadonnée).
        missing, mismatched = [], []
        for m in media.values():
            if m["purged"] or m["state"] == "pending_upload":
                continue
            for kind, column in (("quarantine", "quarantine_key"), ("clean", "clean_key")):
                k = m[column]
                if not k:
                    continue
                got = restored_objects.get((kind, k))
                if got is None:
                    if kind == "clean" and m["state"] == "clean":
                        missing.append(f"{kind}:{m['id']}")
                    elif kind == "quarantine" and m["state"] in ("quarantined", "scanning", "clean"):
                        missing.append(f"{kind}:{m['id']}")
                    continue
                if got[1] != m["sha256"] or got[0] != m["byte_size"]:
                    mismatched.append(f"{kind}:{m['id']}")
        checks["objets_sains_presents"] = not any(x.startswith("clean:") for x in missing)
        checks["objets_quarantaine_presents"] = not any(x.startswith("quarantine:") for x in missing)
        checks["empreintes_conformes"] = not mismatched
        if missing or mismatched:
            checks["ecarts_objets"] = {"absents": missing[:10], "differents": mismatched[:10]}
        checks["objets_restaures"] = len(restored_objects)
        checks["octets_restaures"] = sum(v[0] for v in restored_objects.values())
        checks["objets_conformes_au_manifeste"] = (
            checks["objets_restaures"] == sum(manifest["objects"].values())
            and checks["octets_restaures"] == manifest["object_bytes"]
        )
        # Permissions (D14, 0010) : rôles sans privilège, aucune écriture directe de l'API.
        priv = conn.execute(
            "SELECT (SELECT bool_or(rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname IN ('nl_api','nl_media_scanner','nl_owner')), "
            "has_table_privilege('nl_api','lesson_version_media','INSERT'), "
            "has_column_privilege('nl_api','media_assets','legal_hold','UPDATE'), "
            "has_function_privilege('nl_api','nl_media_attach(uuid,uuid)','EXECUTE'), "
            "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            " AND c.relkind = 'r' AND c.relowner <> 'nl_owner'::regrole)"
        ).fetchone()
        checks["permissions_d14_conservees"] = list(priv) == [False, False, False, True, 0]
        checks["proprietaire_nl_owner"] = priv[4] == 0
        checks["roles_sans_mot_de_passe"] = (
            conn.execute(
                "SELECT count(*) FROM pg_authid WHERE rolname IN ('nl_api','nl_media_scanner') AND rolpassword IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
    verify_head = subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "/checks/verify_head.sql"],
        env=dst_env,
        capture_output=True,
        text=True,
    )
    notice = [line for line in (verify_head.stdout + verify_head.stderr).splitlines() if "OK :" in line]
    checks["verify_head"] = verify_head.returncode == 0 and bool(notice)
    checks["verify_head_detail"] = (
        notice[-1].split("NOTICE:")[-1].strip() if notice else verify_head.stderr.strip()[-160:]
    )
    booleans = [v for k, v in checks.items() if isinstance(v, bool)]
    emit({"ok": all(booleans), "detail_present": bool(detail), **checks})
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sauvegarde et restauration isolée des médias (D22)")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--out", default="/backup")
    b.add_argument("--stamp")
    b.add_argument("--inventory", default=None, help="inventaire du stockage (JSON de s3_provision --inventory)")
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    v.add_argument("--tamper", action="store_true")
    v.add_argument("--wrong-key", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("--manifest", required=True)
    args = p.parse_args(argv)
    return {"backup": backup, "verify": verify, "restore": restore}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

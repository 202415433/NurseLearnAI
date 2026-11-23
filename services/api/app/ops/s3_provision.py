"""Provisionnement des identités S3 RustFS (correctif R3) — à exécuter UNIQUEMENT avec la clé
d'administration (racine) RustFS, dans le conteneur ponctuel `s3-init`, jamais dans l'API ni le worker.

Usage : python -m app.ops.s3_provision            (création ou mise à jour idempotente, puis vérification)
        python -m app.ops.s3_provision --check    (vérification seule, sans écriture)

Trois identités, trois politiques IAM (langage AWS, appliqué par RustFS aux utilisateurs IAM) :
  nl-api-uploader   : écrire (et retirer ce qu'elle vient d'écrire) en QUARANTAINE ; rien d'autre.
  nl-media-scanner  : lire la quarantaine ; écrire, relire et retirer sa propre copie dans le
                      compartiment SAIN ; lister les deux pour la réconciliation ; rien d'autre.
  nl-media-reader   : LIRE le compartiment sain ; rien d'autre (préparée pour 2.2.3-d, non utilisée).
Aucune politique de compartiment (aucun accès anonyme), aucune action d'administration (`admin:*`),
aucune création ou suppression de compartiment pour les identités applicatives.

API d'administration RustFS (`/rustfs/admin/v3`, signature AWS SigV4, service « s3 ») :
add-canned-policy, add-user, set-user-or-group-policy, user-info. Les secrets ne sont jamais affichés.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API_USER = "nl-api-uploader"
SCANNER_USER = "nl-media-scanner"
READER_USER = "nl-media-reader"


class ProvisionError(RuntimeError):
    pass


def policies(quarantine: str, clean: str) -> dict[str, dict[str, Any]]:
    """Matrice IAM (une politique par identité, du même nom que l'identité)."""

    def doc(*statements: dict[str, Any]) -> dict[str, Any]:
        return {"Version": "2012-10-17", "Statement": list(statements)}

    def allow(actions: list[str], resources: list[str]) -> dict[str, Any]:
        return {"Effect": "Allow", "Action": actions, "Resource": resources}

    q_bucket, q_objects = f"arn:aws:s3:::{quarantine}", f"arn:aws:s3:::{quarantine}/*"
    c_bucket, c_objects = f"arn:aws:s3:::{clean}", f"arn:aws:s3:::{clean}/*"
    return {
        # Téléversement : PutObject (le multipartite est couvert par PutObject et borné au même
        # compartiment) ; DeleteObject pour retirer l'objet qu'elle vient d'écrire si l'écriture en base
        # échoue. Aucune lecture : l'API ne relit jamais les octets (ils sont validés AVANT l'envoi).
        API_USER: doc(allow(["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], [q_objects])),
        SCANNER_USER: doc(
            allow(["s3:GetObject"], [q_objects]),
            allow(["s3:ListBucket"], [q_bucket, c_bucket]),
            # Copie validée, vérification d'empreinte (HeadObject relève de GetObject) et retrait de sa
            # propre copie si le verdict n'a pas pu être enregistré.
            allow(["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], [c_objects]),
        ),
        READER_USER: doc(allow(["s3:GetObject"], [c_objects])),
    }


@dataclass(frozen=True)
class Admin:
    endpoint: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"

    def call(self, method: str, path: str, query: dict[str, str], body: bytes | None = None) -> bytes:
        from botocore.auth import SigV4Auth  # type: ignore[import-untyped]
        from botocore.awsrequest import AWSRequest  # type: ignore[import-untyped]
        from botocore.credentials import Credentials  # type: ignore[import-untyped]

        if not self.endpoint.startswith(("http://", "https://")):
            raise ProvisionError("S3_ENDPOINT doit être une URL http(s)")
        url = f"{self.endpoint.rstrip('/')}/rustfs/admin/v3/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)
        payload = body or b""
        headers = {"x-amz-content-sha256": hashlib.sha256(payload).hexdigest()}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = AWSRequest(method=method, url=url, data=payload, headers=headers)
        SigV4Auth(Credentials(self.access_key, self.secret_key), "s3", self.region).add_auth(req)
        http = urllib.request.Request(  # noqa: S310 - schéma http(s) vérifié ci-dessus
            url,
            data=payload if body is not None else None,
            method=method,
            headers=dict(req.headers.items()),
        )
        try:
            with urllib.request.urlopen(http, timeout=15) as resp:  # noqa: S310 - point de terminaison configuré
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise ProvisionError(f"{method} {path} : HTTP {exc.code} {detail}") from None
        except OSError as exc:
            raise ProvisionError(f"{method} {path} : RustFS injoignable ({type(exc).__name__})") from None


def _s3(endpoint: str, access_key: str, secret_key: str, region: str) -> Any:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 2},
            connect_timeout=5,
            read_timeout=15,
        ),
    )


def ensure_private_buckets(s3: Any, buckets: list[str]) -> list[str]:
    """Crée les compartiments manquants (privés) ; refuse toute politique de compartiment existante."""
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    created = []
    for bucket in buckets:
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) not in ("404", "NoSuchBucket", "NotFound"):
                raise ProvisionError(f"compartiment {bucket} : {exc}") from None
            s3.create_bucket(Bucket=bucket)
            created.append(bucket)
        try:
            s3.get_bucket_policy(Bucket=bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchBucketPolicy", "404", "NoSuchPolicy", "NotFound"):
                continue
            raise ProvisionError(f"politique du compartiment {bucket} illisible : {code}") from None
        raise ProvisionError(f"le compartiment {bucket} porte une politique (accès hors IAM) : à retirer")
    return created


def provision(admin: Admin, secrets: dict[str, str], quarantine: str, clean: str, prefix: str = "") -> dict[str, Any]:
    """Crée ou met à jour (idempotent) compartiments, politiques, identités et rattachements."""
    values = list(secrets.values())
    for user, secret in secrets.items():
        # Clés secrètes S3 : 40 caractères au plus (limite héritée de MinIO, appliquée par RustFS).
        if not 32 <= len(secret) <= 40 or not secret.isalnum():
            raise ProvisionError(f"secret de {prefix}{user} invalide : 32 à 40 caractères [A-Za-z0-9]")
    if len(set(values)) != len(values):
        raise ProvisionError("secrets S3 réutilisés entre identités : un secret distinct par service")
    created = ensure_private_buckets(
        _s3(admin.endpoint, admin.access_key, admin.secret_key, admin.region), [quarantine, clean]
    )
    for user, document in policies(quarantine, clean).items():
        if user not in secrets:
            continue
        name = f"{prefix}{user}"
        admin.call("PUT", "add-canned-policy", {"name": name}, json.dumps(document).encode())
        admin.call(
            "PUT",
            "add-user",
            {"accessKey": name},
            json.dumps({"secretKey": secrets[user], "status": "enabled"}).encode(),
        )
        admin.call("PUT", "set-user-or-group-policy", {"policyName": name, "userOrGroup": name, "isGroup": "false"})
    return {"buckets_created": created, **check(admin, list(secrets), prefix)}


def check(admin: Admin, users: list[str], prefix: str = "") -> dict[str, Any]:
    """Vérifie chaque identité : existe, active, rattachée à SA politique seulement (sans secret)."""
    out: dict[str, Any] = {}
    for user in users:
        name = f"{prefix}{user}"
        info = json.loads(admin.call("GET", "user-info", {"accessKey": name}) or b"{}")
        policy = str(info.get("policyName") or info.get("policy") or "")
        status = str(info.get("status") or "")
        out[name] = {"status": status, "policy": policy, "ok": status == "enabled" and policy == name}
    out["ok"] = all(v["ok"] for k, v in out.items() if isinstance(v, dict))
    return out


def inventory(s3: Any, buckets: list[str]) -> dict[str, int]:
    """Compte les objets (intégrité avant / après validation) ; compartiment absent = -1."""
    counts: dict[str, int] = {}
    for bucket in buckets:
        try:
            counts[bucket] = sum(
                page.get("KeyCount", 0) for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
            )
        except Exception:  # compartiment absent ou illisible
            counts[bucket] = -1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provisionnement des identités S3 RustFS (R3)")
    parser.add_argument("--check", action="store_true", help="vérification seule")
    parser.add_argument(
        "--inventory", action="store_true", help="nombre d'objets par compartiment (lecture seule, aucun contenu)"
    )
    args = parser.parse_args(argv)
    env = os.environ
    try:
        admin = Admin(
            env["S3_ENDPOINT"],
            env["S3_ADMIN_ACCESS_KEY"],
            env["S3_ADMIN_SECRET_KEY"],
            env.get("S3_REGION", "us-east-1"),
        )
        quarantine = env.get("MEDIA_QUARANTINE_BUCKET", "nl-media-quarantine")
        clean = env.get("MEDIA_CLEAN_BUCKET", "nl-media-clean")
        secrets = {
            API_USER: env.get("S3_API_SECRET_KEY", ""),
            SCANNER_USER: env.get("S3_SCANNER_SECRET_KEY", ""),
            READER_USER: env.get("S3_READER_SECRET_KEY", ""),
        }
        if args.inventory:
            result = {
                "ok": True,
                **inventory(_s3(admin.endpoint, admin.access_key, admin.secret_key, admin.region), [quarantine, clean]),
            }
        elif args.check:
            result = check(admin, list(secrets))
        else:
            result = provision(admin, secrets, quarantine, clean)
    except KeyError as exc:
        print(json.dumps({"ok": False, "error": f"variable absente : {exc.args[0]}"}))
        return 2
    except ProvisionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

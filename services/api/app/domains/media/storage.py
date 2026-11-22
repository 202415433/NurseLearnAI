"""Stockage S3 PRIVÉ des médias (RustFS, ADR-0011, D19).

* deux compartiments privés : quarantaine (dépôt) et sains (alimenté par l'analyse en 2.2.3-c) ;
* clés aléatoires `uuid/uuid`, jamais dérivées d'un nom de fichier ni d'un identifiant métier ;
* AUCUNE URL publique ni présignée : ce module n'en produit pas, et aucune politique de compartiment
  n'est posée (un compartiment S3 est privé par défaut) ;
* en 2.2.3-b, l'API n'écrit qu'en quarantaine et ne lit jamais les octets.

Le client boto3 (synchrone) est appelé dans un fil d'exécution séparé pour ne pas bloquer l'API.
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings

log = logging.getLogger("nurselearn.media.storage")


class StorageUnavailable(RuntimeError):
    """Stockage injoignable ou en erreur : le téléversement échoue proprement (503), sans fuite."""


def random_key() -> str:
    return f"{uuid.uuid4()}/{uuid.uuid4()}"


@lru_cache(maxsize=8)
def _client(endpoint: str, access_key: str, secret_key: str, region: str) -> Any:
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
            connect_timeout=3,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


class MediaStorage:
    def __init__(self, settings: Settings) -> None:
        if not (settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key):
            raise StorageUnavailable("Stockage des médias non configuré")
        self.client = _client(settings.s3_endpoint, settings.s3_access_key, settings.s3_secret_key, settings.s3_region)
        self.quarantine = settings.media_quarantine_bucket
        self.clean = settings.media_clean_bucket

    def _ensure_bucket(self, bucket: str) -> None:
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in ("404", "NoSuchBucket", "NotFound"):
                raise
            # Création privée : aucune ACL publique, aucune politique de compartiment.
            self.client.create_bucket(Bucket=bucket)

    def _put_quarantine(self, key: str, path: Path, sha256_hex: str) -> None:
        self._ensure_bucket(self.quarantine)
        with path.open("rb") as body:
            self.client.put_object(
                Bucket=self.quarantine,
                Key=key,
                Body=body,
                ContentType="application/octet-stream",  # jamais interprété : non diffusable
                Metadata={"sha256": sha256_hex},
            )

    async def put_quarantine(self, key: str, path: Path, sha256_hex: str) -> None:
        try:
            await run_in_threadpool(self._put_quarantine, key, path, sha256_hex)
        except Exception as exc:  # erreurs réseau, authentification, service indisponible
            log.warning("stockage_indisponible", extra={"operation": "put", "error": type(exc).__name__})
            raise StorageUnavailable("Stockage des médias indisponible") from None

    async def delete_quarantine(self, key: str) -> None:
        """Nettoyage d'un objet que CETTE requête vient d'écrire (transaction refusée ensuite)."""
        try:
            await run_in_threadpool(self.client.delete_object, Bucket=self.quarantine, Key=key)
        except Exception as exc:
            log.warning("nettoyage_impossible", extra={"operation": "delete", "error": type(exc).__name__})

"""Stockage S3 PRIVÉ des médias (RustFS, ADR-0011, D19).

* deux compartiments privés : quarantaine (dépôt) et sains (alimenté par l'analyse en 2.2.3-c) ;
* clés aléatoires `uuid/uuid`, jamais dérivées d'un nom de fichier ni d'un identifiant métier ;
* AUCUNE URL publique ni présignée : ce module n'en produit pas, et aucune politique de compartiment
  n'est posée (un compartiment S3 est privé par défaut) ;
* en 2.2.3-b, l'API n'écrit qu'en quarantaine et ne lit jamais les octets ;
* en 2.2.3-c, seul le worker d'analyse lit la quarantaine et écrit dans le compartiment sain,
  après un verdict « clean » (méthodes synchrones `*_sync`, hors boucle asynchrone) ;
  il ne supprime jamais un objet qu'il n'a pas lui-même écrit lors de la même tentative.

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


class ObjectMissing(RuntimeError):
    """Objet attendu absent du stockage (supprimé hors application)."""


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

    # ------------------------------------------------------------------ worker d'analyse (synchrone)
    def download_quarantine_sync(self, key: str, dest: Path, max_bytes: int) -> int:
        """Copie l'objet de quarantaine dans `dest` (au plus max_bytes + 1 octets lus) ; renvoie la taille."""
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        try:
            obj = self.client.get_object(Bucket=self.quarantine, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                raise ObjectMissing(key) from None
            raise StorageUnavailable(code or "erreur S3") from None
        except Exception as exc:
            raise StorageUnavailable(type(exc).__name__) from None
        total = 0
        try:
            with dest.open("wb") as out:
                for chunk in obj["Body"].iter_chunks(1 << 20):
                    total += len(chunk)
                    if total > max_bytes:
                        break  # taille incohérente : l'appelant refuse, inutile de lire davantage
                    out.write(chunk)
        except Exception as exc:
            raise StorageUnavailable(type(exc).__name__) from None
        finally:
            obj["Body"].close()
        return total

    def put_clean_sync(self, key: str, path: Path, sha256_hex: str) -> None:
        """Dépose la copie validée (octets vérifiés et analysés) dans le compartiment sain, privé."""
        try:
            self._ensure_bucket(self.clean)
            with path.open("rb") as body:
                self.client.put_object(
                    Bucket=self.clean,
                    Key=key,
                    Body=body,
                    ContentType="application/octet-stream",
                    Metadata={"sha256": sha256_hex},
                )
            head = self.client.head_object(Bucket=self.clean, Key=key)
        except Exception as exc:
            raise StorageUnavailable(type(exc).__name__) from None
        if head.get("Metadata", {}).get("sha256") != sha256_hex:
            raise StorageUnavailable("empreinte de la copie saine incohérente")

    def delete_own_clean_copy_sync(self, key: str) -> None:
        """Retire une copie saine écrite par la tentative en cours et jamais enregistrée en base."""
        try:
            self.client.delete_object(Bucket=self.clean, Key=key)
        except Exception as exc:
            log.warning("nettoyage_copie_impossible", extra={"error": type(exc).__name__})

    def list_keys_sync(self, bucket: str, limit: int = 100_000) -> set[str]:
        """Clés d'un compartiment (réconciliation) ; compartiment absent = ensemble vide."""
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        keys: set[str] = set()
        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    keys.add(item["Key"])
                    if len(keys) >= limit:
                        return keys
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) in ("NoSuchBucket", "404", "NotFound"):
                return keys
            raise StorageUnavailable(type(exc).__name__) from None
        except Exception as exc:
            raise StorageUnavailable(type(exc).__name__) from None
        return keys

"""Sonde R3 exécutée DANS un conteneur applicatif, avec SES identifiants S3 (aucune clé fournie à la sonde).

Usage : docker compose cp scripts/r3_s3_probe.py api:/tmp/ && docker compose exec -T api python /tmp/r3_s3_probe.py api
        (idem avec media-worker … scanner)
Aucune opération susceptible d'altérer un compartiment existant n'est tentée (ni ACL, ni suppression de
compartiment : couvertes par les tests sur les compartiments de test). Les opérations interdites visent
des clés ALÉATOIRES inexistantes : un refus renvoie 403, une permission
accordée renverrait 404 ou 204 (et serait signalée). Aucun objet existant n'est lu, modifié ni supprimé.
Code de sortie 1 si une opération interdite n'est pas refusée (403) ou si une opération permise échoue.
Sortie : une ligne JSON {"identity": ..., "refused": n, "total": n, "unexpected": [...], "allowed_ok": [...]}.
"""

import json
import sys
import uuid

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.domains.media.storage import _client


def main(role: str) -> None:
    s = get_settings()
    c = _client(s.s3_endpoint, s.s3_access_key, s.s3_secret_key, s.s3_region)
    q, k = s.media_quarantine_bucket, s.media_clean_bucket

    def rnd() -> str:
        return f"r3-sonde/{uuid.uuid4()}"

    forbidden = {
        "creer_compartiment": lambda: c.create_bucket(Bucket=f"nl-r3-intrus-{uuid.uuid4().hex[:8]}"),
        "politique_compartiment": lambda: c.put_bucket_policy(
            Bucket=k, Policy='{"Version":"2012-10-17","Statement":[]}'
        ),
    }
    allowed: dict = {}
    if role == "api":
        forbidden |= {
            "lire_sain": lambda: c.get_object(Bucket=k, Key=rnd()),
            "ecrire_sain": lambda: c.put_object(Bucket=k, Key=rnd(), Body=b"sonde"),
            "supprimer_sain": lambda: c.delete_object(Bucket=k, Key=rnd()),
            "lister_sain": lambda: c.list_objects_v2(Bucket=k, MaxKeys=1),
            "lire_quarantaine": lambda: c.get_object(Bucket=q, Key=rnd()),
            "lister_quarantaine": lambda: c.list_objects_v2(Bucket=q, MaxKeys=1),
            "multipartite_sain": lambda: c.create_multipart_upload(Bucket=k, Key=rnd()),
        }
    elif role == "scanner":
        forbidden |= {
            "ecrire_quarantaine": lambda: c.put_object(Bucket=q, Key=rnd(), Body=b"sonde"),
            "supprimer_quarantaine": lambda: c.delete_object(Bucket=q, Key=rnd()),
            "multipartite_quarantaine": lambda: c.create_multipart_upload(Bucket=q, Key=rnd()),
        }
        allowed = {
            "lister_quarantaine": lambda: c.list_objects_v2(Bucket=q, MaxKeys=1),
            "lister_sain": lambda: c.list_objects_v2(Bucket=k, MaxKeys=1),
        }
    else:
        raise SystemExit("rôle attendu : api | scanner")

    unexpected, refused = [], 0
    for name, op in forbidden.items():
        try:
            op()
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 403:
                refused += 1
                continue
            unexpected.append(f"{name}:{status}")
        else:
            unexpected.append(f"{name}:accepte")
    allowed_ok = []
    for name, op in allowed.items():
        try:
            op()
            allowed_ok.append(name)
        except ClientError as exc:
            unexpected.append(f"{name}:refuse:{exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
    ok = not unexpected and refused == len(forbidden) and len(allowed_ok) == len(allowed)
    print(
        json.dumps(
            {
                "identity": s.s3_access_key,
                "refused": refused,
                "total": len(forbidden),
                "unexpected": unexpected,
                "allowed_ok": allowed_ok,
                "ok": ok,
            }
        )
    )
    if not ok:
        raise SystemExit(1)


main(sys.argv[1] if len(sys.argv) > 1 else "")

"""R3 — identités S3 distinctes sur RustFS RÉEL (politiques IAM appliquées par le serveur).

Exécuté seulement contre RustFS (NL_TEST_S3_KIND=rustfs) : un serveur S3 de mise au point n'applique pas
les politiques IAM. La clé d'administration (S3_ACCESS_KEY / S3_SECRET_KEY, racine de la pile de test) sert
UNIQUEMENT au provisionnement et aux vérifications ; chaque test agit ensuite avec l'identité restreinte.
Identités de test préfixées `nl-test-`, secrets aléatoires propres à l'exécution, jamais affichés.
"""

# ruff: noqa: F811 - fixtures pytest importées puis utilisées comme paramètres
from __future__ import annotations

import hashlib
import os
import secrets
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from botocore.exceptions import ClientError
from test_media_upload import QUARANTINE, S3, S3_KEY, S3_SECRET, png

from app.ops.s3_provision import API_USER, READER_USER, SCANNER_USER, Admin, ProvisionError, _s3, provision

CLEAN = "nl-test-media-clean"
PREFIX = "nl-test-"
REAL = os.environ.get("NL_TEST_S3_KIND") == "rustfs"

pytestmark = pytest.mark.skipif(not (S3 and REAL), reason="identités S3 : RustFS réel requis (NL_TEST_S3_KIND=rustfs)")


def refused_or_unsupported(call: Callable[[], Any]) -> None:
    """ACL : refus (403) ou fonction non prise en charge par le serveur (501) ; jamais acceptée."""
    with pytest.raises(ClientError) as exc:
        call()
    assert exc.value.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (403, 501), exc.value


def denied(call: Callable[[], Any]) -> str:
    """L'opération DOIT être refusée par le serveur (403 / AccessDenied) ; renvoie le code."""
    with pytest.raises(ClientError) as exc:
        call()
    code = str(exc.value.response.get("Error", {}).get("Code", ""))
    status = exc.value.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    assert code in ("AccessDenied", "Forbidden", "403") or status == 403, code
    return code


@pytest.fixture(scope="module")
def ids() -> dict[str, Any]:
    admin = Admin(S3 or "", S3_KEY, S3_SECRET)
    keys = {API_USER: secrets.token_hex(16), SCANNER_USER: secrets.token_hex(16), READER_USER: secrets.token_hex(16)}
    result = provision(admin, keys, QUARANTINE, CLEAN, prefix=PREFIX)
    assert result["ok"], {k: v for k, v in result.items() if k != "buckets_created"}
    clients = {u: _s3(S3 or "", f"{PREFIX}{u}", k, "us-east-1") for u, k in keys.items()}
    return {"admin": admin, "root": _s3(S3 or "", S3_KEY, S3_SECRET, "us-east-1"), "keys": keys, **clients}


def key() -> str:
    return f"{uuid.uuid4()}/{uuid.uuid4()}"


def body(seed: str = "") -> bytes:
    return png(size=(17, 13)) + seed.encode()


# =============================================================================================
# Provisionnement : identités actives, une politique chacune, aucune politique de compartiment
# =============================================================================================
def test_identities_are_provisioned_with_their_own_policy_only(ids):
    from app.ops.s3_provision import check

    res = check(ids["admin"], [API_USER, SCANNER_USER, READER_USER], PREFIX)
    assert res["ok"], res
    for bucket in (QUARANTINE, CLEAN):  # aucun accès hors IAM
        with pytest.raises(ClientError) as exc:
            ids["root"].get_bucket_policy(Bucket=bucket)
        assert "Policy" in str(exc.value.response.get("Error", {}).get("Code", "")) or "404" in str(exc.value)


# =============================================================================================
# 1–4. Identité API : écrit en quarantaine, rien d'autre
# =============================================================================================
def test_api_identity_uploads_to_quarantine_only(ids):
    api, root = ids[API_USER], ids["root"]
    k = key()
    api.put_object(Bucket=QUARANTINE, Key=k, Body=body("q"), ContentType="application/octet-stream")  # 1
    assert root.get_object(Bucket=QUARANTINE, Key=k)["Body"].read() == body("q")
    clean_key = key()
    root.put_object(Bucket=CLEAN, Key=clean_key, Body=body("sain"))
    denied(lambda: api.get_object(Bucket=CLEAN, Key=clean_key))  # 2
    denied(lambda: api.head_object(Bucket=CLEAN, Key=clean_key))
    denied(lambda: api.put_object(Bucket=CLEAN, Key=key(), Body=b"x"))  # 3
    denied(lambda: api.put_object(Bucket=CLEAN, Key=clean_key, Body=b"ecrase"))
    denied(lambda: api.delete_object(Bucket=CLEAN, Key=clean_key))  # 4
    denied(lambda: api.get_object(Bucket=QUARANTINE, Key=k))  # l'API ne relit jamais les octets
    denied(lambda: api.list_objects_v2(Bucket=QUARANTINE))
    denied(lambda: api.list_objects_v2(Bucket=CLEAN))
    assert root.get_object(Bucket=CLEAN, Key=clean_key)["Body"].read() == body("sain")


# =============================================================================================
# 5–7. Identité worker : lit la quarantaine, écrit le sain, n'administre rien
# =============================================================================================
def test_scanner_identity_reads_quarantine_and_writes_clean(ids):
    scanner, root = ids[SCANNER_USER], ids["root"]
    qk, ck = key(), key()
    root.put_object(Bucket=QUARANTINE, Key=qk, Body=body("a-analyser"))
    assert scanner.get_object(Bucket=QUARANTINE, Key=qk)["Body"].read() == body("a-analyser")  # 5
    scanner.put_object(Bucket=CLEAN, Key=ck, Body=body("valide"), Metadata={"sha256": "x"})  # 6
    assert scanner.head_object(Bucket=CLEAN, Key=ck)["Metadata"]["sha256"] == "x"
    assert "Contents" in scanner.list_objects_v2(Bucket=QUARANTINE, MaxKeys=1)
    denied(lambda: scanner.put_object(Bucket=QUARANTINE, Key=key(), Body=b"x"))
    denied(lambda: scanner.delete_object(Bucket=QUARANTINE, Key=qk))
    for op in (  # 7
        lambda: scanner.create_bucket(Bucket=f"nl-test-intrus-{uuid.uuid4().hex[:8]}"),
        lambda: scanner.delete_bucket(Bucket=CLEAN),
        lambda: scanner.put_bucket_policy(Bucket=CLEAN, Policy='{"Version":"2012-10-17","Statement":[]}'),
    ):
        denied(op)
    refused_or_unsupported(lambda: scanner.put_bucket_acl(Bucket=CLEAN, ACL="public-read"))
    refused_or_unsupported(lambda: scanner.put_object_acl(Bucket=CLEAN, Key=ck, ACL="public-read"))


# =============================================================================================
# 8–10. Identité de lecture : lit le sain, rien d'autre
# =============================================================================================
def test_reader_identity_is_read_only_on_clean(ids):
    reader, root = ids[READER_USER], ids["root"]
    ck, qk = key(), key()
    root.put_object(Bucket=CLEAN, Key=ck, Body=body("lecture"))
    root.put_object(Bucket=QUARANTINE, Key=qk, Body=body("quarantaine"))
    assert reader.get_object(Bucket=CLEAN, Key=ck)["Body"].read() == body("lecture")  # 8
    denied(lambda: reader.get_object(Bucket=QUARANTINE, Key=qk))  # 9
    denied(lambda: reader.put_object(Bucket=CLEAN, Key=key(), Body=b"x"))  # 10
    denied(lambda: reader.put_object(Bucket=CLEAN, Key=ck, Body=b"ecrase"))
    denied(lambda: reader.delete_object(Bucket=CLEAN, Key=ck))
    denied(lambda: reader.list_objects_v2(Bucket=CLEAN))
    denied(lambda: reader.put_object(Bucket=QUARANTINE, Key=key(), Body=b"x"))
    assert root.get_object(Bucket=CLEAN, Key=ck)["Body"].read() == body("lecture")


# =============================================================================================
# 11. Accès anonyme ; 12. élévation de privilèges ; 13. multipartite
# =============================================================================================
def test_anonymous_access_is_refused(ids):
    k = key()
    ids["root"].put_object(Bucket=CLEAN, Key=k, Body=b"prive")
    for method, url, data in (
        ("GET", f"{S3}/{CLEAN}/{k}", None),
        ("GET", f"{S3}/{QUARANTINE}/", None),
        ("GET", f"{S3}/{CLEAN}/", None),
        ("PUT", f"{S3}/{QUARANTINE}/{key()}", b"x"),
        ("DELETE", f"{S3}/{CLEAN}/{k}", None),
    ):
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - test de non-exposition
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        assert exc.value.code == 403, (method, url)


def test_privilege_escalation_attempts_fail(ids):
    for user in (API_USER, SCANNER_USER, READER_USER):  # 12
        me = Admin(S3 or "", f"{PREFIX}{user}", ids["keys"][user])
        for path, query, payload in (
            ("add-user", {"accessKey": f"{PREFIX}intrus"}, b'{"secretKey":"' + b"z" * 40 + b'","status":"enabled"}'),
            (
                "add-canned-policy",
                {"name": f"{PREFIX}tout"},
                b'{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:*","admin:*"],"Resource":["*"]}]}',
            ),
            (
                "set-user-or-group-policy",
                {"policyName": "consoleAdmin", "userOrGroup": f"{PREFIX}{user}", "isGroup": "false"},
                None,
            ),
            (
                "set-user-or-group-policy",
                {"policyName": f"{PREFIX}{SCANNER_USER}", "userOrGroup": f"{PREFIX}{user}", "isGroup": "false"},
                None,
            ),
        ):
            with pytest.raises(ProvisionError) as exc:
                me.call("PUT", path, query, payload)
            assert "HTTP 403" in str(exc.value) or "HTTP 401" in str(exc.value), (user, path)
        denied(
            lambda u=user: ids[u].put_bucket_policy(
                Bucket=QUARANTINE,
                Policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":"*","Action":["s3:GetObject"],"Resource":["arn:aws:s3:::x/*"]}]}',
            )
        )
    from app.ops.s3_provision import check

    assert check(ids["admin"], [API_USER, SCANNER_USER, READER_USER], PREFIX)["ok"]  # rien n'a changé


def test_multipart_does_not_bypass_permissions(ids):
    api, scanner, reader, root = ids[API_USER], ids[SCANNER_USER], ids[READER_USER], ids["root"]
    for client, bucket in ((api, CLEAN), (reader, CLEAN), (scanner, QUARANTINE), (reader, QUARANTINE)):  # 13
        denied(lambda c=client, b=bucket: c.create_multipart_upload(Bucket=b, Key=key()))
    # Copie côté serveur de la quarantaine vers le sain par l'API : refusée (lecture et écriture manquent).
    qk = key()
    api.put_object(Bucket=QUARANTINE, Key=qk, Body=body("copie"))
    denied(lambda: api.copy_object(Bucket=CLEAN, Key=key(), CopySource={"Bucket": QUARANTINE, "Key": qk}))
    # Le multipartite autorisé reste borné : l'API envoie en plusieurs parties en quarantaine seulement.
    mk = key()
    up = api.create_multipart_upload(Bucket=QUARANTINE, Key=mk)
    part = api.upload_part(Bucket=QUARANTINE, Key=mk, UploadId=up["UploadId"], PartNumber=1, Body=b"m" * 1024)
    api.complete_multipart_upload(
        Bucket=QUARANTINE,
        Key=mk,
        UploadId=up["UploadId"],
        MultipartUpload={"Parts": [{"ETag": part["ETag"], "PartNumber": 1}]},
    )
    assert root.get_object(Bucket=QUARANTINE, Key=mk)["Body"].read() == b"m" * 1024


# =============================================================================================
# Compromission de l'identité API : aucun objet sain ne peut être altéré
# =============================================================================================
def test_compromised_api_identity_cannot_alter_a_clean_object(ids):
    api, root = ids[API_USER], ids["root"]
    ck, original = key(), body("objet-sain-de-reference")
    root.put_object(Bucket=CLEAN, Key=ck, Body=original, Metadata={"sha256": hashlib.sha256(original).hexdigest()})
    qk = key()
    api.put_object(Bucket=QUARANTINE, Key=qk, Body=b"charge malveillante fictive")
    attacks = (
        lambda: api.put_object(Bucket=CLEAN, Key=ck, Body=b"remplace"),
        lambda: api.delete_object(Bucket=CLEAN, Key=ck),
        lambda: api.copy_object(Bucket=CLEAN, Key=ck, CopySource={"Bucket": QUARANTINE, "Key": qk}),
        lambda: api.create_multipart_upload(Bucket=CLEAN, Key=ck),
        lambda: api.put_object_acl(Bucket=CLEAN, Key=ck, ACL="public-read"),
        lambda: api.put_bucket_policy(Bucket=CLEAN, Policy='{"Version":"2012-10-17","Statement":[]}'),
        lambda: api.put_object_tagging(Bucket=CLEAN, Key=ck, Tagging={"TagSet": [{"Key": "x", "Value": "y"}]}),
    )
    for attack in attacks:
        try:
            attack()
        except ClientError as exc:
            assert exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (403, 400, 501), exc
        else:
            pytest.fail("une opération de l'identité API sur le compartiment sain a été acceptée")
    # Suppression groupée : S3 peut répondre 200 avec une erreur par objet ; aucun objet ne doit être supprimé.
    try:
        res = api.delete_objects(Bucket=CLEAN, Delete={"Objects": [{"Key": ck}]})
    except ClientError as exc:
        assert exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (403, 400, 501), exc
    else:
        assert not res.get("Deleted") and res.get("Errors"), res
    after = root.get_object(Bucket=CLEAN, Key=ck)
    assert after["Body"].read() == original
    assert after["Metadata"]["sha256"] == hashlib.sha256(original).hexdigest()


# =============================================================================================
# Chaîne applicative réelle (MediaStorage) avec les identités restreintes : téléversement par
# l'API, analyse et copie saine par le worker, lecture par l'identité de lecture.
# =============================================================================================
def test_application_storage_chain_works_with_restricted_identities(ids, tmp_path):
    from app.core.config import get_settings
    from app.domains.media.storage import MediaStorage

    base = get_settings()

    def storage(user: str) -> MediaStorage:
        return MediaStorage(
            base.model_copy(
                update={
                    "s3_endpoint": S3,
                    "s3_access_key": f"{PREFIX}{user}",
                    "s3_secret_key": ids["keys"][user],
                    "media_quarantine_bucket": QUARANTINE,
                    "media_clean_bucket": CLEAN,
                }
            )
        )

    data = body("chaine")
    digest = hashlib.sha256(data).hexdigest()
    src = tmp_path / "src.bin"
    src.write_bytes(data)
    k = key()

    api = storage(API_USER)
    api._put_quarantine(k, src, digest)  # HeadBucket 403 toléré : compartiment créé par le provisionnement

    scanner = storage(SCANNER_USER)
    dest = tmp_path / "dl.bin"
    assert scanner.download_quarantine_sync(k, dest, len(data)) == len(data)
    assert dest.read_bytes() == data
    scanner.put_clean_sync(k, dest, digest)  # écriture + vérification d'intégrité (HeadObject)
    assert k in scanner.list_keys_sync(CLEAN)

    assert ids[READER_USER].get_object(Bucket=CLEAN, Key=k)["Body"].read() == data
    denied(lambda: api.client.get_object(Bucket=CLEAN, Key=k))
    denied(lambda: api.client.put_object(Bucket=CLEAN, Key=k, Body=b"altere"))
    assert ids["root"].get_object(Bucket=CLEAN, Key=k)["Body"].read() == data
    for b in (QUARANTINE, CLEAN):
        ids["root"].delete_object(Bucket=b, Key=k)

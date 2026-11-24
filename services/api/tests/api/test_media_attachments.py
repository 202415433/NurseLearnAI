"""Rattachement des médias (D3-3) et conservation légale par l'API (2.2.3-e, sous-lot E1).

Chaîne réelle : téléversement par l'API, analyse par le worker (ClamAV réel en CI et sous Windows),
rattachement par `PUT /lesson-versions/{id}/media/{media_id}` (fonction contrôlée nl_media_attach,
migration 0010), workflow éditorial, publication d'une édition PUBLIQUE et NON MÉDICALE, diffusion.
L'application est connectée en `nl_api` (D14) : aucune écriture directe sur les tables protégées.
Comptes et contenus FICTIFS (`@example.test`).
"""

# ruff: noqa: F811 - fixtures pytest importées puis utilisées comme paramètres
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from test_editorial import (  # noqa: F401 - fixtures partagées
    E,
    World,
    account,
    approve,
    new_lesson,
    new_module,
    new_version,
    ok,
    sql,
    submit,
    w,
)
from test_media_delivery import (  # noqa: F401 - fixtures partagées
    PUBLISHED,
    VTT,
    D,
    archive_published,
    big_mp4,
    clean_media,
    delivery_client,
    publish_with_media,
    reader_creds,
    worker,
)
from test_media_scan import (  # noqa: F401 - fixtures partagées
    CLAMD_HOST,
    CLAMD_KIND,
    REDIS,
    clamd,
    eicar,
    planted,
    scanner_dsn,
)
from test_media_upload import S3, M, api, png, reserve, uploaded  # noqa: F401 - fixtures partagées

pytestmark = pytest.mark.skipif(
    not (S3 and REDIS and (CLAMD_HOST or CLAMD_KIND == "fake")),
    reason="rattachement des médias : S3, Redis et ClamAV (NL_TEST_CLAMD_HOST) requis",
)


def lv_draft(api: TestClient, w: World, author: Any, org: str | None = None) -> str:
    mod = new_module(api, w, author, org=org)
    les = new_lesson(api, author, mod["id"])
    return str(ok(new_version(api, author, les["id"]), 201)["id"])


def put(api: TestClient, who: Any, lv: str, mid: str) -> Any:
    return api.put(f"{E}/lesson-versions/{lv}/media/{mid}", headers=who.h)


def code(r: Any) -> str:
    return str(r.json().get("code"))


# =============================================================================================
# Rattachement / détachement
# =============================================================================================
def test_attach_list_detach_on_a_draft(api: TestClient, w: World, worker: Any) -> None:
    lv = lv_draft(api, w, w.author)
    mid = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(12, 9)))
    pending = uploaded(api, w.author, "image", "image/png", png(with_metadata=False))["id"]  # en quarantaine
    r = put(api, w.author, lv, mid)
    assert r.status_code == 201 and [x["media_id"] for x in r.json()["items"]] == [mid]
    assert put(api, w.author, lv, mid).status_code == 200  # idempotent
    assert put(api, w.author, lv, pending).status_code == 201  # brouillon : analyse en cours admise
    listing = ok(api.get(f"{E}/lesson-versions/{lv}/media", headers=w.author.h))
    assert listing["editable"] is True and {x["state"] for x in listing["items"]} == {"clean", "quarantined"}
    for item in listing["items"]:  # aucune clé de stockage ni adresse
        assert not {"clean_key", "quarantine_key", "url"} & set(item)
    # La soumission exige des médias sains : refus tant que l'analyse n'est pas terminée.
    r = submit(api, w.author, "lesson-versions", lv)
    assert r.status_code == 422 and code(r) == "media_not_clean"
    assert api.delete(f"{E}/lesson-versions/{lv}/media/{pending}", headers=w.author.h).status_code == 204
    assert api.delete(f"{E}/lesson-versions/{lv}/media/{pending}", headers=w.author.h).status_code == 404


def test_attach_refusals_without_revealing_other_establishments(
    api: TestClient, w: World, worker: Any, sync_engine: Engine
) -> None:
    lv_public = lv_draft(api, w, w.author)
    lv_org = lv_draft(api, w, w.org_instr, org=w.org_a)
    public = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(5, 5)))
    org_media = clean_media(
        api, worker, w.org_instr, "image", "image/png", png(with_metadata=False, size=(6, 5)), organization_id=w.org_a
    )
    # Sans session, puis sans droit éditorial.
    assert api.put(f"{E}/lesson-versions/{lv_public}/media/{public}").status_code == 401
    assert put(api, w.student, lv_public, public).status_code in (403, 404)
    # Autre établissement : ni la leçon ni le média ne sont révélés (404, jamais 403 ni 422).
    for r in (put(api, w.other_instr, lv_org, public), put(api, w.org_instr, lv_public, public)):
        assert r.status_code in (403, 404), r.text
    r = put(api, w.other_instr, lv_public, org_media)
    assert r.status_code in (403, 404) and w.org_a not in r.text
    assert put(api, w.org_instr, lv_org, str(uuid.uuid4())).status_code == 404
    # Portée cohérente : média d'établissement sur une leçon publique → 422 media_scope (auteur plateforme).
    r = put(api, w.author, lv_public, org_media)
    assert r.status_code == 422 and code(r) == "media_scope"
    # Un auteur d'établissement ne voit pas la bibliothèque publique (portée 2.2.2) : 404 ;
    # il rattache les médias de son établissement.
    assert put(api, w.org_instr, lv_org, public).status_code == 404
    assert put(api, w.org_instr, lv_org, org_media).status_code == 201
    assert (
        sql(sync_engine, "SELECT count(*) FROM lesson_version_media WHERE lesson_version_id = :l", l=lv_public).scalar()
        == 0
    )


def test_unhealthy_or_revoked_media_cannot_be_attached(
    api: TestClient, w: World, worker: Any, sync_engine: Engine
) -> None:
    lv = lv_draft(api, w, w.author)
    reserved = ok(reserve(api, w.author, "image", "image/png", 100), 201)["id"]  # jamais téléversé
    infected = planted(api, None, sync_engine, eicar(), who=w.author)
    assert worker.process(infected) == "infected"
    revoked = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(4, 4)))
    ok(api.post(f"{M}/{revoked}/revoke", headers=w.reviewer.h, json={"reason": "Retrait fictif (test)"}))
    for mid in (reserved, infected, revoked):
        r = put(api, w.author, lv, mid)
        assert r.status_code == 422 and code(r) == "media_not_clean", (mid, r.text)
    deleted = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(3, 4)))
    assert api.delete(f"{M}/{deleted}", headers=w.author.h).status_code == 204
    assert put(api, w.author, lv, deleted).status_code == 422


def test_frozen_versions_and_published_editions_are_never_modified(
    api: TestClient, w: World, worker: Any, sync_engine: Engine, reader_creds: tuple[str, str]
) -> None:
    image = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(10, 10)))
    extra = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(11, 10)))
    release, lv, _ = publish_with_media(api, w, sync_engine, w.author, [image])
    for r in (put(api, w.author, lv, extra), api.delete(f"{E}/lesson-versions/{lv}/media/{image}", headers=w.author.h)):
        assert r.status_code == 409 and code(r) == "not_draft", r.text
    listing = ok(api.get(f"{E}/lesson-versions/{lv}/media", headers=w.author.h))
    assert listing["editable"] is False and [x["media_id"] for x in listing["items"]] == [image]
    # Le média rattaché par l'API est diffusé dans l'édition publiée ; le média non rattaché ne l'est pas.
    learner = account(api, sync_engine)
    with delivery_client(reader_creds) as c:
        url = f"{D}/{{}}/content?release={release}&lesson_version={lv}"
        assert c.get(url.format(image), headers=learner.h).status_code == 200
        assert c.get(url.format(extra), headers=learner.h).status_code == 404


def test_video_attachment_requires_clean_captions_before_submission(api: TestClient, w: World, worker: Any) -> None:
    lv = lv_draft(api, w, w.author)
    video = clean_media(api, worker, w.author, "video", "video/mp4", big_mp4(20_000))
    assert put(api, w.author, lv, video).status_code == 201
    r = submit(api, w.author, "lesson-versions", lv)
    assert r.status_code == 422 and code(r) == "missing_captions"
    clean_media(api, worker, w.author, "captions", "text/vtt", VTT, captions_for=video)
    listing = ok(api.get(f"{E}/lesson-versions/{lv}/media", headers=w.author.h))
    assert listing["items"][0]["captions_id"] is not None


# =============================================================================================
# Conservation légale
# =============================================================================================
@pytest.fixture(scope="module")
def admin(api: TestClient, sync_engine: Engine) -> Iterator[Any]:
    yield account(api, sync_engine, ("admin", None))


def test_legal_hold_set_release_history_and_audit(
    api: TestClient, w: World, worker: Any, admin: Any, sync_engine: Engine
) -> None:
    mid = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(8, 3)))
    body = {"hold": True, "reason": "Enquête administrative fictive"}
    for who in (w.author, w.reviewer, w.org_admin):
        assert api.post(f"{M}/{mid}/legal-hold", headers=who.h, json=body).status_code in (403, 404)
    assert api.post(f"{M}/{mid}/legal-hold", json=body).status_code == 401
    for bad in ({"hold": True}, {"hold": True, "reason": "abc"}, {"hold": True, "reason": "x" * 1001}):
        assert api.post(f"{M}/{mid}/legal-hold", headers=admin.h, json=bad).status_code == 422
    r = ok(api.post(f"{M}/{mid}/legal-hold", headers=admin.h, json=body))
    assert r["legal_hold"] is True and "legal_hold_reason" not in r
    r = api.post(f"{M}/{mid}/legal-hold", headers=admin.h, json=body)
    assert r.status_code == 409 and code(r) == "no_change"
    d = api.delete(f"{M}/{mid}", headers=w.author.h)
    assert d.status_code == 409 and "legal_hold" in d.text
    assert api.get(f"{M}/{mid}/legal-hold", headers=w.author.h).status_code in (403, 404)
    ok(api.post(f"{M}/{mid}/legal-hold", headers=admin.h, json={"hold": False, "reason": "Fin de l'enquête fictive"}))
    history = ok(api.get(f"{M}/{mid}/legal-hold", headers=admin.h))
    assert [(h["action"], h["actor_user_id"]) for h in history] == [("set", admin.user_id), ("released", admin.user_id)]
    assert history[0]["reason"] == "Enquête administrative fictive" and history[0]["created_at"]
    actions = sql(
        sync_engine,
        "SELECT string_agg(action, ',' ORDER BY action) FROM audit_logs WHERE entity_id = :m AND action LIKE 'media.legal_hold%'",
        m=mid,
    ).scalar()
    assert (
        actions
        == "media.legal_hold_released,media.legal_hold_requested,media.legal_hold_requested,media.legal_hold_set"
    )
    meta = sql(
        sync_engine, "SELECT string_agg(metadata::text, ' ') FROM audit_logs WHERE entity_id = :m", m=mid
    ).scalar()
    assert "Enquête" not in meta  # aucun texte libre dans l'audit
    assert api.delete(f"{M}/{mid}", headers=w.author.h).status_code == 204


def test_legal_hold_never_makes_an_infected_or_revoked_media_servable(
    api: TestClient, w: World, worker: Any, admin: Any, sync_engine: Engine, reader_creds: tuple[str, str]
) -> None:
    infected = planted(api, None, sync_engine, eicar(), who=w.author)
    assert worker.process(infected) == "infected"
    revoked = clean_media(api, worker, w.author, "image", "image/png", png(with_metadata=False, size=(2, 7)))
    ok(api.post(f"{M}/{revoked}/revoke", headers=w.reviewer.h, json={"reason": "Retrait fictif (test)"}))
    with delivery_client(reader_creds) as c:
        for mid in (infected, revoked):
            ok(
                api.post(
                    f"{M}/{mid}/legal-hold", headers=admin.h, json={"hold": True, "reason": "Preuve conservée (test)"}
                )
            )
            assert sql(sync_engine, "SELECT legal_hold FROM media_assets WHERE id = :m", m=mid).scalar() is True
            for who in (w.author, admin):
                assert c.get(f"{D}/{mid}/preview", headers=who.h).status_code == 404

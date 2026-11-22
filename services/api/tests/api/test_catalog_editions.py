"""Catalogue public lu à travers l'édition publiée (étape 2.2.2-c).

Contenus FICTIFS et NON MÉDICAUX, menés par l'API éditoriale avec des comptes de test
(`@example.test`) ; aucune publication médicale. Métier INTL/care_assistant_generic, pour ne pas
toucher aux compteurs du pilote PAB Québec.
"""

# ruff: noqa: F811 - fixtures pytest importées de test_editorial puis utilisées comme paramètres
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from test_editorial import (  # noqa: F401 - fixtures partagées (api, w, api_flag_on)
    DUE,
    E,
    World,
    api,
    approve,
    new_lesson,
    new_module,
    new_program,
    ok,
    para,
    sql,
    submit,
    w,
)

C = "/api/v1/programs"


def approved_lesson_version(
    api: TestClient, w: World, lesson_id: str, title: str, locale: str = "fr", jurisdiction: str | None = None
) -> str:
    body: dict[str, Any] = {"locale": locale, "title": title, "body": para(f"Contenu fictif : {title}")}
    if jurisdiction:
        body["jurisdiction_id"] = jurisdiction
    lv = ok(api.post(f"{E}/lessons/{lesson_id}/versions", headers=w.author.h, json=body), 201)
    ok(submit(api, w.author, "lesson-versions", lv["id"]))
    ok(approve(api, w.reviewer, "lesson-versions", lv["id"]))
    return lv["id"]


def approved_module_version(api: TestClient, w: World, module_id: str, lessons: list[tuple[str, int, dict]]) -> str:
    items = [{"lesson_id": lid, "position": pos, "versions": versions} for lid, pos, versions in lessons]
    mv = ok(api.post(f"{E}/modules/{module_id}/versions", headers=w.author.h, json={"lessons": items}), 201)
    ok(submit(api, w.author, "module-versions", mv["id"]))
    ok(approve(api, w.reviewer, "module-versions", mv["id"]))
    return mv["id"]


def published_release(
    api: TestClient,
    w: World,
    program_id: str,
    modules: list[tuple[str, int]],
    prerequisites: list | None = None,
    *,
    publish: bool = True,
) -> dict:
    rel = ok(
        api.post(
            f"{E}/programs/{program_id}/releases",
            headers=w.author.h,
            json={
                "modules": [{"module_version_id": mv, "position": pos} for mv, pos in modules],
                "prerequisites": prerequisites or [],
                "change_summary": "Édition fictive (test catalogue)",
            },
        ),
        201,
    )
    ok(submit(api, w.author, "releases", rel["id"]))
    ok(approve(api, w.reviewer, "releases", rel["id"]))
    if publish:
        return ok(api.post(f"{E}/releases/{rel['id']}/publish", headers=w.reviewer.h))
    return rel


def listed_codes(api: TestClient, **params: str) -> dict[str, int]:
    page = ok(api.get(C, params={"jurisdiction": "INTL", "limit": "100", **params}))
    return {p["code"]: p["module_count"] for p in page["items"]}


def archive(api: TestClient, w: World, release_id: str) -> None:
    ok(api.post(f"{E}/releases/{release_id}/archive", headers=w.reviewer.h, json={"reason": "Fin du test catalogue"}))


# --------------------------------------------------------------------------------------------
def test_complete_edition_order_prerequisites_and_frozen_positions(api: TestClient, w: World) -> None:
    prog = new_program(api, w, w.author)
    m1, m2 = new_module(api, w, w.author), new_module(api, w, w.author)
    l1a, l1b = new_lesson(api, w.author, m1["id"], 0), new_lesson(api, w.author, m1["id"], 1)
    l2 = new_lesson(api, w.author, m2["id"], 0)
    v1a = approved_lesson_version(api, w, l1a["id"], "Leçon A (test)")
    v1b = approved_lesson_version(api, w, l1b["id"], "Leçon B (test)")
    v2 = approved_lesson_version(api, w, l2["id"], "Leçon C (test)")
    # Positions FIGÉES par la version de module (inverse de l'ordre de travail des leçons).
    mv1 = approved_module_version(api, w, m1["id"], [(l1a["id"], 5, {"fr": v1a}), (l1b["id"], 2, {"fr": v1b})])
    mv2 = approved_module_version(api, w, m2["id"], [(l2["id"], 0, {"fr": v2})])
    rel = published_release(api, w, prog["id"], [(mv2, 0), (mv1, 1)], [{"module_id": m1["id"], "requires": m2["id"]}])
    try:
        # Plus de parcours incomplet : la structure de travail (program_modules) est vide, et pourtant
        # l'édition publiée expose ses deux modules.
        assert listed_codes(api)[prog["code"]] == 2
        d = ok(api.get(f"{C}/{prog['id']}"))
        assert d["release_no"] == 1
        assert [m["code"] for m in d["modules"]] == [m2["code"], m1["code"]]
        assert [m["position"] for m in d["modules"]] == [0, 1]
        assert d["modules"][1]["prerequisites"] == [m2["code"]] and d["modules"][0]["prerequisites"] == []
        assert [(x["code"], x["position"]) for x in d["modules"][1]["lessons"]] == [(l1b["code"], 2), (l1a["code"], 5)]
    finally:
        archive(api, w, rel["id"])
    assert api.get(f"{C}/{prog['id']}").status_code == 404
    assert prog["code"] not in listed_codes(api)


def test_two_programs_share_a_module_at_different_versions(api: TestClient, w: World) -> None:
    """Plusieurs versions publiées d'une même leçon : chaque programme sert EXACTEMENT la sienne."""
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    v1 = approved_lesson_version(api, w, les["id"], "Version 1 (test)")
    v2 = approved_lesson_version(api, w, les["id"], "Version 2 (test)")
    mv1 = approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": v1})])
    mv2 = approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": v2})])
    pa, pb = new_program(api, w, w.author), new_program(api, w, w.author)
    ra = published_release(api, w, pa["id"], [(mv1, 0)])
    rb = published_release(api, w, pb["id"], [(mv2, 0)])
    try:
        assert ok(api.get(f"{C}/{pa['id']}"))["modules"][0]["lessons"][0]["title"] == "Version 1 (test)"
        assert ok(api.get(f"{C}/{pb['id']}"))["modules"][0]["lessons"][0]["title"] == "Version 2 (test)"
        # Une version plus récente, même publiée ailleurs, ne remplace jamais implicitement celle de l'édition.
        v3 = approved_lesson_version(api, w, les["id"], "Version 3 brouillon puis approuvée (test)")
        draft = ok(
            api.post(
                f"{E}/lessons/{les['id']}/versions",
                headers=w.author.h,
                json={"locale": "fr", "title": "Brouillon (test)", "body": para("Brouillon")},
            ),
            201,
        )
        assert v3 and draft["status"] == "draft"
        assert ok(api.get(f"{C}/{pa['id']}"))["modules"][0]["lessons"][0]["title"] == "Version 1 (test)"
    finally:
        archive(api, w, ra["id"])
        archive(api, w, rb["id"])


def test_new_edition_replaces_the_archived_one(api: TestClient, w: World, sync_engine: Engine) -> None:
    prog = new_program(api, w, w.author)
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    v1 = approved_lesson_version(api, w, les["id"], "Première (test)")
    r1 = published_release(
        api, w, prog["id"], [(approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": v1})]), 0)]
    )
    v2 = approved_lesson_version(api, w, les["id"], "Seconde (test)")
    mv2 = approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": v2})])
    # Édition 2 approuvée mais NON publiée : le catalogue sert toujours l'édition 1.
    r2 = published_release(api, w, prog["id"], [(mv2, 0)], publish=False)
    d = ok(api.get(f"{C}/{prog['id']}"))
    assert d["release_no"] == 1 and d["modules"][0]["lessons"][0]["title"] == "Première (test)"
    ok(api.post(f"{E}/releases/{r2['id']}/publish", headers=w.reviewer.h))
    assert sql(sync_engine, "SELECT status FROM program_releases WHERE id = :r", r=r1["id"]).scalar() == "archived"
    # L'ancienne version reste « published » (historique 0006) mais n'est plus servie.
    assert sql(sync_engine, "SELECT status FROM lesson_versions WHERE id = :v", v=v1).scalar() == "published"
    d = ok(api.get(f"{C}/{prog['id']}"))
    assert d["release_no"] == 2 and d["modules"][0]["lessons"][0]["title"] == "Seconde (test)"
    archive(api, w, r2["id"])


def test_languages_use_only_versions_cited_by_the_edition(api: TestClient, w: World) -> None:
    prog = new_program(api, w, w.author)
    mod = new_module(api, w, w.author)
    both, fr_only = new_lesson(api, w.author, mod["id"], 0), new_lesson(api, w.author, mod["id"], 1)
    fr1 = approved_lesson_version(api, w, both["id"], "Leçon bilingue (test)")
    en1 = approved_lesson_version(api, w, both["id"], "Bilingual lesson (test)", locale="en")
    fr2 = approved_lesson_version(api, w, fr_only["id"], "Leçon en français seulement (test)")
    # Une version anglaise de la seconde leçon existe et est approuvée, mais l'édition ne la cite pas.
    approved_lesson_version(api, w, fr_only["id"], "Uncited English (test)", locale="en")
    mv = approved_module_version(
        api, w, mod["id"], [(both["id"], 0, {"fr": fr1, "en": en1}), (fr_only["id"], 1, {"fr": fr2})]
    )
    rel = published_release(api, w, prog["id"], [(mv, 0)])
    try:
        fr = ok(api.get(f"{C}/{prog['id']}", headers={"Accept-Language": "fr"}))["modules"][0]["lessons"]
        en = ok(api.get(f"{C}/{prog['id']}", headers={"Accept-Language": "en"}))["modules"][0]["lessons"]
        assert [(x["title"], x["locale"]) for x in fr] == [
            ("Leçon bilingue (test)", "fr"),
            ("Leçon en français seulement (test)", "fr"),
        ]
        assert [(x["title"], x["locale"]) for x in en] == [
            ("Bilingual lesson (test)", "en"),
            ("Leçon en français seulement (test)", "fr"),  # repli sur la langue de base CITÉE
        ]
    finally:
        archive(api, w, rel["id"])


def test_jurisdiction_specific_versions_and_filters(api: TestClient, w: World, sync_engine: Engine) -> None:
    intl = str(sql(sync_engine, "SELECT id FROM jurisdictions WHERE code = 'INTL'").scalar())
    qc = str(sql(sync_engine, "SELECT id FROM jurisdictions WHERE code = 'CA-QC'").scalar())
    prog = new_program(api, w, w.author)
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    local = approved_lesson_version(api, w, les["id"], "Version locale INTL (test)", jurisdiction=intl)
    other = approved_lesson_version(api, w, les["id"], "Version Québec (test)", jurisdiction=qc)
    # Une version d'une autre juridiction ne peut pas entrer dans l'édition (refus de la base).
    bad_mv = approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": other})])
    r = api.post(
        f"{E}/programs/{prog['id']}/releases",
        headers=w.author.h,
        json={"modules": [{"module_version_id": bad_mv, "position": 0}], "change_summary": "Essai"},
    )
    bad = ok(r, 201)
    res = submit(api, w.author, "releases", bad["id"])
    assert res.status_code == 422 and res.json()["code"] == "incoherent_reference"
    ok(api.post(f"{E}/releases/{bad['id']}/abandon", headers=w.author.h, json={"reason": "Juridiction incohérente"}))
    mv = approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": local})])
    rel = published_release(api, w, prog["id"], [(mv, 0)])
    try:
        lesson = ok(api.get(f"{C}/{prog['id']}"))["modules"][0]["lessons"][0]
        assert lesson["title"] == "Version locale INTL (test)" and lesson["jurisdiction_specific"] is True
        assert prog["code"] in listed_codes(api)
        page = ok(api.get(C, params={"jurisdiction": "CA-QC", "limit": "100"}))
        assert prog["code"] not in {p["code"] for p in page["items"]}
    finally:
        archive(api, w, rel["id"])


def test_drafts_and_unpublished_editions_are_never_exposed(api: TestClient, w: World, sync_engine: Engine) -> None:
    prog = new_program(api, w, w.author)
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    v = approved_lesson_version(api, w, les["id"], "Approuvée non publiée (test)")
    # Ancienne structure de travail remplie, mais aucune édition publiée : rien n'est exposé.
    sql(
        sync_engine,
        "INSERT INTO program_modules (program_id, module_id, position) VALUES (:p, :m, 0)",
        p=prog["id"],
        m=mod["id"],
    )
    published_release(
        api,
        w,
        prog["id"],
        [(approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": v})]), 0)],
        publish=False,
    )
    assert api.get(f"{C}/{prog['id']}").status_code == 404
    assert prog["code"] not in listed_codes(api)
    profession = ok(api.get("/api/v1/professions", params={"jurisdiction": "INTL", "limit": "100"}))
    assert all(p["availability"]["published"] >= 0 for p in profession["items"])


def test_incoherent_published_edition_is_hidden(api: TestClient, w: World, sync_engine: Engine) -> None:
    """Un module devenu privé après publication rend l'édition incohérente : programme masqué (404)."""
    prog = new_program(api, w, w.author)
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    v = approved_lesson_version(api, w, les["id"], "Leçon (test)")
    rel = published_release(
        api, w, prog["id"], [(approved_module_version(api, w, mod["id"], [(les["id"], 0, {"fr": v})]), 0)]
    )
    assert api.get(f"{C}/{prog['id']}").status_code == 200

    def published_count() -> int:
        items = ok(api.get("/api/v1/professions", params={"jurisdiction": "INTL", "limit": "100"}))["items"]
        return next(p["availability"]["published"] for p in items if p["code"] == "care_assistant_generic")

    before = published_count()
    sql(sync_engine, "UPDATE modules SET organization_id = :o WHERE id = :m", o=w.org_a, m=mod["id"])
    try:
        assert api.get(f"{C}/{prog['id']}").status_code == 404
        assert prog["code"] not in listed_codes(api)
        assert published_count() == before - 1  # disponibilités alignées sur l'exposition
    finally:
        sql(sync_engine, "UPDATE modules SET organization_id = NULL WHERE id = :m", m=mod["id"])
        archive(api, w, rel["id"])


def test_establishment_editions_stay_private(api: TestClient, w: World) -> None:
    prog = new_program(api, w, w.org_instr, org=w.org_a)
    mod = new_module(api, w, w.org_instr, org=w.org_a)
    les = new_lesson(api, w.org_instr, mod["id"])
    lv = ok(
        api.post(
            f"{E}/lessons/{les['id']}/versions",
            headers=w.org_instr.h,
            json={"locale": "fr", "title": "Leçon privée (test)", "body": para("Privé")},
        ),
        201,
    )
    ok(submit(api, w.org_instr, "lesson-versions", lv["id"]))
    ok(api.post(f"{E}/lesson-versions/{lv['id']}/approve", headers=w.org_rev.h, json={"next_review_due": DUE}))
    mv = ok(
        api.post(
            f"{E}/modules/{mod['id']}/versions",
            headers=w.org_instr.h,
            json={"lessons": [{"lesson_id": les["id"], "position": 0, "versions": {"fr": lv["id"]}}]},
        ),
        201,
    )
    ok(submit(api, w.org_instr, "module-versions", mv["id"]))
    ok(approve(api, w.org_rev, "module-versions", mv["id"]))
    rel = ok(
        api.post(
            f"{E}/programs/{prog['id']}/releases",
            headers=w.org_instr.h,
            json={"modules": [{"module_version_id": mv["id"], "position": 0}], "change_summary": "Édition privée"},
        ),
        201,
    )
    ok(submit(api, w.org_instr, "releases", rel["id"]))
    ok(approve(api, w.org_rev, "releases", rel["id"]))
    ok(api.post(f"{E}/releases/{rel['id']}/publish", headers=w.org_rev.h))
    assert api.get(f"{C}/{prog['id']}").status_code == 404
    assert prog["code"] not in listed_codes(api)
    ok(api.post(f"{E}/releases/{rel['id']}/archive", headers=w.org_rev.h, json={"reason": "Fin du test"}))

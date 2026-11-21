"""API éditoriale 2.2.2-b contre une vraie base (PostgreSQL 16, migrations 0001→0006) et Redis.

Comptes : UNIQUEMENT des comptes de test (`@example.test`), créés par l'inscription réelle puis dotés
de rôles par la garde de test `nl.role_grant_bypass` (comme les tests d'identité). Aucun relecteur
réel n'est simulé ; les contenus sont fictifs. La publication RÉELLE d'un programme médical n'est
jamais effectuée : le drapeau `medical_publication_enabled` reste désactivé, et le client où il est
activé ne sert qu'à prouver que les contrôles de publication refusent une édition non conforme.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.domains.editorial.errors import CODES, LEGACY_MESSAGES, code_for
from app.main import create_app

PASSWORD = "Une phrase de passe solide 2026"
TOKEN_RE = re.compile(r"jeton=([A-Za-z0-9_-]{20,})")
E = "/api/v1/editorial"
SCHEMA_DIR = Path(__file__).resolve().parents[4] / "database" / "schema"
SQL_LEAK = re.compile(r"(?i)\b(insert|update|select|constraint|violates|sqlstate|psycopg|asyncpg|lesson_versions)\b")


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "cors_origins": ["http://localhost:3000"],
        "rate_limit_salt": uuid.uuid4().hex,
        "rate_limit_per_minute": 0,
        "mail_backend": "memory",
        "register_per_hour_per_ip": 10_000,
        "login_per_10min_per_ip": 10_000,
        "email_actions_per_hour_per_ip": 10_000,
        "email_actions_per_hour_per_address": 10_000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@dataclass
class Acct:
    email: str
    token: str
    user_id: str

    @property
    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def sql(engine: Engine, query: str, **params: object) -> Any:
    with engine.begin() as conn:
        return conn.execute(text(query), params)


def _token(c: TestClient, address: str) -> str:
    for mail in reversed(c.app.state.mailer.outbox):  # type: ignore[attr-defined]
        if mail.to == address:
            m = TOKEN_RE.search(mail.body)
            if m:
                return m.group(1)
    raise AssertionError("aucun lien")


def account(c: TestClient, engine: Engine, *roles: tuple[str, str | None]) -> Acct:
    address = f"ed-{uuid.uuid4().hex[:10]}@example.test"
    r = c.post(
        "/api/v1/auth/register",
        json={
            "email": address,
            "password": PASSWORD,
            "display_name": "Compte de test",
            "locale": "fr",
            "adult_attestation": True,
            "accept_terms": True,
            "accept_privacy": True,
        },
    )
    assert r.status_code == 202, r.text
    assert c.post("/api/v1/auth/verify-email", json={"token": _token(c, address)}).status_code == 200
    user_id = str(sql(engine, "SELECT id FROM users WHERE email = :e", e=address).scalar())
    for role, org in roles:
        with engine.begin() as conn:
            conn.execute(text("SELECT set_config('nl.role_grant_bypass', 'on', true)"))
            conn.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id, organization_id) "
                    "SELECT :u, id, :o FROM roles WHERE code = :r"
                ),
                {"u": user_id, "o": org, "r": role},
            )
    r = c.post("/api/v1/auth/login", json={"email": address, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return Acct(address, r.json()["session_token"], user_id)


def make_org(engine: Engine) -> str:
    tag = uuid.uuid4().hex[:8]
    return str(
        sql(
            engine,
            "INSERT INTO organizations (slug, name, kind) VALUES (:s, :n, 'school') RETURNING id",
            s=f"ed-org-{tag}",
            n=f"Institut test {tag}",
        ).scalar()
    )


def code() -> str:
    return f"t-{uuid.uuid4().hex[:10]}"


def ok(r: Any, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def problem_code(r: Any) -> str | None:
    assert r.headers["content-type"].startswith("application/problem+json"), r.text
    body = r.json()
    assert not SQL_LEAK.search(body.get("detail", "")), body
    return body.get("code")


def para(text_: str, block: str = "p1") -> dict[str, Any]:
    return {"schema": "blocks/v1", "blocks": [{"id": block, "type": "paragraph", "text": text_}]}


DUE = (date.today() + timedelta(days=365)).isoformat()


# --------------------------------------------------------------------------------------------
# Monde de test
# --------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def api() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


@pytest.fixture(scope="module")
def api_flag_on() -> Iterator[TestClient]:
    """Drapeau activé : sert UNIQUEMENT à prouver que les contrôles refusent une édition non conforme."""
    with TestClient(create_app(_settings(medical_publication_enabled=True))) as c:
        yield c


@dataclass
class World:
    prof: str
    spec: str
    org_a: str
    org_b: str
    author: Acct
    author2: Acct
    reviewer: Acct
    reviewer2: Acct
    org_instr: Acct
    org_rev: Acct
    other_instr: Acct
    org_admin: Acct
    student: Acct


@pytest.fixture(scope="module")
def w(api: TestClient, sync_engine: Engine) -> World:
    prof = str(
        sql(
            sync_engine,
            "SELECT p.id FROM professions p JOIN jurisdictions j ON j.id = p.jurisdiction_id "
            "WHERE j.code = 'INTL' AND p.code = 'care_assistant_generic'",
        ).scalar()
    )
    spec = str(sql(sync_engine, "SELECT id FROM specialties WHERE code = 'care_assistant_core'").scalar())
    org_a, org_b = make_org(sync_engine), make_org(sync_engine)
    return World(
        prof=prof,
        spec=spec,
        org_a=org_a,
        org_b=org_b,
        author=account(api, sync_engine, ("instructor", None)),
        author2=account(api, sync_engine, ("instructor", None)),
        reviewer=account(api, sync_engine, ("medical_reviewer", None)),
        reviewer2=account(api, sync_engine, ("medical_reviewer", None), ("instructor", None)),
        org_instr=account(api, sync_engine, ("instructor", org_a)),
        org_rev=account(api, sync_engine, ("medical_reviewer", org_a)),
        other_instr=account(api, sync_engine, ("instructor", org_b)),
        org_admin=account(api, sync_engine, ("org_admin", org_a)),
        student=account(api, sync_engine),
    )


def new_program(api: TestClient, w: World, who: Acct, *, org: str | None = None, medical: bool = False) -> dict:
    body: dict[str, Any] = {
        "code": code(),
        "profession_id": w.prof,
        "title": {"fr": "Programme fictif (test)"},
        "level": "beginner",
        "is_medical": medical,
    }
    if org:
        body |= {"organization_id": org, "access_tier": "organization"}
    return ok(api.post(f"{E}/programs", headers=who.h, json=body), 201)


def new_module(api: TestClient, w: World, who: Acct, *, org: str | None = None) -> dict:
    body: dict[str, Any] = {
        "code": code(),
        "specialty_id": w.spec,
        "title": {"fr": "Module fictif (test)"},
        "level": "beginner",
    }
    if org:
        body |= {"organization_id": org, "access_tier": "organization"}
    return ok(api.post(f"{E}/modules", headers=who.h, json=body), 201)


def new_lesson(api: TestClient, who: Acct, module_id: str, position: int = 0) -> dict:
    return ok(
        api.post(f"{E}/modules/{module_id}/lessons", headers=who.h, json={"code": code(), "position": position}), 201
    )


def new_version(api: TestClient, who: Acct, lesson_id: str, body: dict | None = None) -> Any:
    return api.post(
        f"{E}/lessons/{lesson_id}/versions",
        headers=who.h,
        json={"locale": "fr", "title": "Leçon fictive (test)", "body": body or para("Contenu fictif non médical")},
    )


def submit(api: TestClient, who: Acct, kind: str, entity_id: str) -> Any:
    return api.post(f"{E}/{kind}/{entity_id}/submit", headers=who.h)


def approve(api: TestClient, who: Acct, kind: str, entity_id: str, due: str | None = DUE) -> Any:
    payload = {"next_review_due": due} if kind == "lesson-versions" and due else {}
    return api.post(f"{E}/{kind}/{entity_id}/approve", headers=who.h, json=payload)


def approved_chain(
    api: TestClient,
    w: World,
    *,
    medical: bool = False,
    org: str | None = None,
    author: Acct | None = None,
    reviewer: Acct | None = None,
) -> dict[str, Any]:
    """Programme → module → leçon → version de leçon → version de module → édition, tous approuvés."""
    author = author or w.author
    reviewer = reviewer or w.reviewer
    prog = new_program(api, w, author, org=org, medical=medical)
    mod = new_module(api, w, author, org=org)
    les = new_lesson(api, author, mod["id"])
    lv = ok(new_version(api, author, les["id"]), 201)
    ref = None
    if medical:
        ref = ok(
            api.post(
                f"{E}/references",
                headers=author.h,
                json={"kind": "guideline", "title": f"Source fictive {code()}", "url": "https://example.test/guide"},
            ),
            201,
        )
        ok(api.post(f"{E}/references/{ref['id']}/verify", headers=w.reviewer.h))
        got = api.get(f"{E}/lesson-versions/{lv['id']}", headers=author.h)
        ok(
            api.put(
                f"{E}/lesson-versions/{lv['id']}/sources",
                headers={**author.h, "If-Match": got.headers["ETag"]},
                json={"references": [{"reference_id": ref["id"]}]},
            )
        )
    ok(submit(api, author, "lesson-versions", lv["id"]))
    ok(approve(api, reviewer, "lesson-versions", lv["id"]))
    mv = ok(
        api.post(
            f"{E}/modules/{mod['id']}/versions",
            headers=author.h,
            json={"lessons": [{"lesson_id": les["id"], "position": 0, "versions": {"fr": lv["id"]}}]},
        ),
        201,
    )
    ok(submit(api, author, "module-versions", mv["id"]))
    ok(approve(api, reviewer, "module-versions", mv["id"]))
    rel = ok(
        api.post(
            f"{E}/programs/{prog['id']}/releases",
            headers=author.h,
            json={"modules": [{"module_version_id": mv["id"], "position": 0}], "change_summary": "Première édition"},
        ),
        201,
    )
    ok(submit(api, author, "releases", rel["id"]))
    ok(approve(api, reviewer, "releases", rel["id"]))
    return {"program": prog, "module": mod, "lesson": les, "lv": lv, "mv": mv, "release": rel, "ref": ref}


# --------------------------------------------------------------------------------------------
# b1 — fondations
# --------------------------------------------------------------------------------------------
def test_route_inventory_and_no_direct_publication(api: TestClient) -> None:
    paths = api.app.openapi()["paths"]  # type: ignore[attr-defined]
    ops = [(m.upper(), p) for p, item in paths.items() if p.startswith(E) for m in item]
    assert len(ops) == 37
    publishing = [p for _, p in ops if p.endswith("/publish")]
    assert publishing == [f"{E}/releases/{{release_id}}/publish"]  # aucune publication de leçon ou de module


def test_authentication_required(api: TestClient, w: World) -> None:
    assert api.get(f"{E}/programs").status_code == 401
    # Jeton porté UNIQUEMENT par l'en-tête Authorization : un cookie n'authentifie pas (pas de CSRF possible).
    r = api.post(f"{E}/programs", headers={"Cookie": f"nl_session={w.author.token}; session={w.author.token}"}, json={})
    assert r.status_code == 401
    assert api.post(f"{E}/programs", json={}).status_code == 401
    assert api.get(f"{E}/programs", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_strict_contracts_refuse_state_and_identity_fields(api: TestClient, w: World) -> None:
    base = {"code": code(), "profession_id": w.prof, "title": {"fr": "Programme fictif"}, "level": "beginner"}
    for forged in ({"status": "published"}, {"author_id": w.reviewer.user_id}, {"reviewed_by": w.reviewer.user_id}):
        r = api.post(f"{E}/programs", headers=w.author.h, json={**base, **forged})
        assert r.status_code == 422
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    for forged in ({"status": "approved"}, {"author_id": w.reviewer.user_id}, {"content_sha256": "0" * 64}):
        payload = {"locale": "fr", "title": "Leçon fictive", **forged}
        assert api.post(f"{E}/lessons/{les['id']}/versions", headers=w.author.h, json=payload).status_code == 422
    for bad in ("<script>x</script>", "[clic](javascript:alert(1))", "\x07bip"):
        assert new_version(api, w.author, les["id"], para(bad)).status_code == 422
    assert (
        api.post(
            f"{E}/references",
            headers=w.author.h,
            json={
                "kind": "guideline",
                "title": "Source",
                "url": "https://example.test",
                "verified_by": w.author.user_id,
            },
        ).status_code
        == 422
    )


def test_malformed_ids_and_unknown_kinds_are_404(api: TestClient, w: World) -> None:
    assert api.get(f"{E}/programs/pas-un-uuid", headers=w.author.h).status_code == 404
    assert api.get(f"{E}/lesson-versions/{uuid.uuid4()}", headers=w.author.h).status_code == 404
    assert api.get(f"{E}/inconnu/{uuid.uuid4()}/decisions", headers=w.author.h).status_code == 404
    assert api.post(f"{E}/lessons/{uuid.uuid4()}/submit", headers=w.author.h).status_code == 404


def test_rbac_matrix_for_creation(api: TestClient, w: World) -> None:
    body = {"code": code(), "profession_id": w.prof, "title": {"fr": "Programme fictif"}, "level": "beginner"}
    # Catalogue public : permission globale exigée.
    for who in (w.student, w.org_admin, w.org_instr, w.org_rev):
        r = api.post(f"{E}/programs", headers=who.h, json={**body, "code": code()})
        assert r.status_code == 403 and problem_code(r) == "forbidden", who
    # Établissement : rôle dans l'établissement exigé ; inconnu → 404, membre sans permission → 403.
    org_body = {**body, "organization_id": w.org_a, "access_tier": "organization"}
    assert api.post(f"{E}/programs", headers=w.other_instr.h, json={**org_body, "code": code()}).status_code == 404
    assert api.post(f"{E}/programs", headers=w.org_admin.h, json={**org_body, "code": code()}).status_code == 403
    assert api.post(f"{E}/programs", headers=w.org_instr.h, json={**org_body, "code": code()}).status_code == 201
    assert api.post(f"{E}/programs", headers=w.author.h, json={**body, "code": code()}).status_code == 201


def test_establishment_isolation(api: TestClient, w: World) -> None:
    prog = new_program(api, w, w.org_instr, org=w.org_a)
    mod = new_module(api, w, w.org_instr, org=w.org_a)
    les = new_lesson(api, w.org_instr, mod["id"])
    lv = ok(new_version(api, w.org_instr, les["id"]), 201)
    for who in (w.other_instr, w.org_admin, w.student):
        assert api.get(f"{E}/programs/{prog['id']}", headers=who.h).status_code == 404
        assert api.get(f"{E}/lesson-versions/{lv['id']}", headers=who.h).status_code == 404
        assert api.patch(f"{E}/modules/{mod['id']}", headers=who.h, json={"level": "advanced"}).status_code == 404
        assert submit(api, who, "lesson-versions", lv["id"]).status_code == 404
        assert api.get(f"{E}/lesson-versions/{lv['id']}/decisions", headers=who.h).status_code == 404
    listed = {p["id"] for p in ok(api.get(f"{E}/programs", headers=w.other_instr.h))}
    assert prog["id"] not in listed
    assert prog["id"] in {p["id"] for p in ok(api.get(f"{E}/programs", headers=w.org_instr.h))}
    assert prog["id"] in {p["id"] for p in ok(api.get(f"{E}/programs", headers=w.author.h))}  # rôle global
    # Contenu public : invisible pour un formateur d'établissement (pas de permission globale).
    pub = new_program(api, w, w.author)
    assert api.get(f"{E}/programs/{pub['id']}", headers=w.org_instr.h).status_code == 404


def test_sensitive_session_required_for_writes(api: TestClient, w: World, sync_engine: Engine) -> None:
    acct = account(api, sync_engine, ("instructor", None))
    sql(
        sync_engine,
        "UPDATE auth_sessions SET last_seen_at = now() - interval '31 minutes' WHERE user_id = :u",
        u=acct.user_id,
    )
    assert api.get(f"{E}/programs", headers=acct.h).status_code == 200
    r = api.post(
        f"{E}/programs",
        headers=acct.h,
        json={"code": code(), "profession_id": w.prof, "title": {"fr": "Programme fictif"}, "level": "beginner"},
    )
    assert r.status_code == 403 and r.json()["type"].endswith("reauthentication-required")


# --------------------------------------------------------------------------------------------
# b2 — brouillons, ETag, versions
# --------------------------------------------------------------------------------------------
def test_lesson_version_drafts_etag_and_numbering(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    r = new_version(api, w.author, les["id"])
    v1 = ok(r, 201)
    assert v1["version_no"] == 1 and v1["status"] == "draft" and v1["author_id"] == w.author.user_id
    etag = r.headers["ETag"]
    url = f"{E}/lesson-versions/{v1['id']}"
    assert api.patch(url, headers=w.author.h, json={"title": "Sans If-Match"}).status_code == 428
    assert api.patch(url, headers={**w.author.h, "If-Match": '"ancien"'}, json={"title": "Périmé"}).status_code == 412
    r2 = api.patch(url, headers={**w.author.h, "If-Match": etag}, json={"title": "Titre modifié (test)"})
    assert ok(r2)["title"] == "Titre modifié (test)" and r2.headers["ETag"] != etag
    stale = api.patch(url, headers={**w.author.h, "If-Match": etag}, json={"title": "Écrasement"})
    assert stale.status_code == 412 and stale.json()["type"].endswith("stale-draft")
    v2 = ok(api.post(f"{E}/lessons/{les['id']}/versions", headers=w.author.h, json={"from_version_id": v1["id"]}), 201)
    assert v2["version_no"] == 2 and v2["title"] == "Titre modifié (test)"


def test_concurrent_creations_get_distinct_numbers(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: new_version(api, w.author, les["id"]), range(4)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert sorted(r.json()["version_no"] for r in results) == [1, 2, 3, 4]


def test_concurrent_patches_with_same_etag_one_wins(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    r = new_version(api, w.author, les["id"])
    url, etag = f"{E}/lesson-versions/{r.json()['id']}", r.headers["ETag"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda t: api.patch(url, headers={**w.author.h, "If-Match": etag}, json={"title": t}),
                ["Version A (test)", "Version B (test)"],
            )
        )
    assert sorted(r.status_code for r in results) == [200, 412]


def test_approved_versions_are_immutable(api: TestClient, w: World) -> None:
    chain = approved_chain(api, w)
    lv, mv, rel = chain["lv"], chain["mv"], chain["release"]
    got = api.get(f"{E}/lesson-versions/{lv['id']}", headers=w.author.h)
    r = api.patch(
        f"{E}/lesson-versions/{lv['id']}",
        headers={**w.author.h, "If-Match": got.headers["ETag"]},
        json={"title": "Modification interdite"},
    )
    assert r.status_code == 409 and problem_code(r) == "not_draft"
    got = api.get(f"{E}/module-versions/{mv['id']}", headers=w.author.h)
    r = api.patch(
        f"{E}/module-versions/{mv['id']}",
        headers={**w.author.h, "If-Match": got.headers["ETag"]},
        json={"lessons": [{"lesson_id": chain["lesson"]["id"], "position": 1, "versions": {"fr": lv["id"]}}]},
    )
    assert r.status_code == 409 and problem_code(r) == "not_draft"
    got = api.get(f"{E}/releases/{rel['id']}", headers=w.author.h)
    r = api.patch(
        f"{E}/releases/{rel['id']}",
        headers={**w.author.h, "If-Match": got.headers["ETag"]},
        json={"modules": [{"module_version_id": mv["id"], "position": 3}], "change_summary": "Non"},
    )
    assert r.status_code == 409 and problem_code(r) == "not_draft"
    assert api.delete(f"{E}/lesson-versions/{lv['id']}", headers={**w.author.h, "If-Match": "*"}).status_code == 409


def test_module_version_and_release_contracts(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    lv = ok(new_version(api, w.author, les["id"]), 201)
    # Version française obligatoire.
    r = api.post(
        f"{E}/modules/{mod['id']}/versions",
        headers=w.author.h,
        json={"lessons": [{"lesson_id": les["id"], "position": 0, "versions": {"en": lv["id"]}}]},
    )
    assert r.status_code == 422
    # Version d'une autre leçon : refus par la base (clé composée), sans fuite SQL.
    other = new_lesson(api, w.author, new_module(api, w, w.author)["id"])
    r = api.post(
        f"{E}/modules/{mod['id']}/versions",
        headers=w.author.h,
        json={"lessons": [{"lesson_id": other["id"], "position": 0, "versions": {"fr": lv["id"]}}]},
    )
    assert r.status_code == 422 and problem_code(r) == "23503"
    mv = ok(
        api.post(
            f"{E}/modules/{mod['id']}/versions",
            headers=w.author.h,
            json={"lessons": [{"lesson_id": les["id"], "position": 0, "versions": {"fr": lv["id"]}}]},
        ),
        201,
    )
    assert mv["version_no"] == 1 and mv["lessons"][0]["versions"] == {"fr": lv["id"]}
    prog = new_program(api, w, w.author)
    # Prérequis : refus d'un auto-prérequis et d'un module hors édition.
    r = api.post(
        f"{E}/programs/{prog['id']}/releases",
        headers=w.author.h,
        json={
            "modules": [{"module_version_id": mv["id"], "position": 0}],
            "prerequisites": [{"module_id": mod["id"], "requires": str(uuid.uuid4())}],
            "change_summary": "Essai",
        },
    )
    assert r.status_code == 422
    # Version de module d'un autre établissement : invisible (404).
    private_mod = new_module(api, w, w.org_instr, org=w.org_a)
    private_les = new_lesson(api, w.org_instr, private_mod["id"])
    private_lv = ok(new_version(api, w.org_instr, private_les["id"]), 201)
    private_mv = ok(
        api.post(
            f"{E}/modules/{private_mod['id']}/versions",
            headers=w.org_instr.h,
            json={"lessons": [{"lesson_id": private_les["id"], "position": 0, "versions": {"fr": private_lv["id"]}}]},
        ),
        201,
    )
    other_prog = new_program(api, w, w.other_instr, org=w.org_b)
    r = api.post(
        f"{E}/programs/{other_prog['id']}/releases",
        headers=w.other_instr.h,
        json={"modules": [{"module_version_id": private_mv["id"], "position": 0}], "change_summary": "Essai"},
    )
    assert r.status_code == 404
    # Module privé dans un programme public : refus de la base (portée).
    r = api.post(
        f"{E}/programs/{prog['id']}/releases",
        headers=w.author.h,
        json={"modules": [{"module_version_id": private_mv["id"], "position": 0}], "change_summary": "Essai"},
    )
    assert r.status_code == 422 and problem_code(r) == "organization_scope"


def test_diff_between_versions(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    v1 = ok(new_version(api, w.author, les["id"]), 201)
    body = para("Contenu fictif non médical")
    body["blocks"].append({"id": "p2", "type": "paragraph", "text": "Ajout fictif"})
    v2 = ok(
        api.post(
            f"{E}/lessons/{les['id']}/versions", headers=w.author.h, json={"from_version_id": v1["id"], "body": body}
        ),
        201,
    )
    d = ok(api.get(f"{E}/lesson-versions/{v2['id']}/diff", headers=w.author.h, params={"against": v1["id"]}))
    assert d["changes"]["blocks"]["added"] == ["p2"] and d["changes"]["blocks"]["removed"] == []
    other = ok(new_version(api, w.author, new_lesson(api, w.author, mod["id"], 1)["id"]), 201)
    r = api.get(f"{E}/lesson-versions/{v2['id']}/diff", headers=w.author.h, params={"against": other["id"]})
    assert r.status_code == 422


# --------------------------------------------------------------------------------------------
# b3 — workflow, indépendance, historique
# --------------------------------------------------------------------------------------------
def test_workflow_independence_and_history(api: TestClient, w: World, sync_engine: Engine) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    lv = ok(new_version(api, w.author, les["id"]), 201)
    assert approve(api, w.reviewer, "lesson-versions", lv["id"]).status_code == 409  # brouillon
    assert submit(api, w.student, "lesson-versions", lv["id"]).status_code == 404
    assert submit(api, w.reviewer, "lesson-versions", lv["id"]).status_code == 403  # relecteur sans content:create
    ok(submit(api, w.author, "lesson-versions", lv["id"]))
    r = approve(api, w.author, "lesson-versions", lv["id"])
    assert r.status_code == 403 and problem_code(r) in ("forbidden", "self_approval")
    # Auteur qui détient aussi content:review : l'auto-approbation reste interdite.
    lv2 = ok(new_version(api, w.reviewer2, new_lesson(api, w.author, mod["id"], 1)["id"]), 201)
    ok(submit(api, w.reviewer2, "lesson-versions", lv2["id"]))
    r = approve(api, w.reviewer2, "lesson-versions", lv2["id"])
    assert r.status_code == 403 and problem_code(r) == "self_approval"
    assert (
        api.post(
            f"{E}/lesson-versions/{lv2['id']}/request-changes", headers=w.reviewer2.h, json={"comment": "Auto-renvoi"}
        ).status_code
        == 403
    )
    # Date de révision obligatoire et future.
    for due in (None, date.today().isoformat(), (date.today() + timedelta(days=5000)).isoformat()):
        r = approve(api, w.reviewer, "lesson-versions", lv["id"], due=due)
        assert r.status_code == 422 and problem_code(r) == "review_date_required"
    # Demande de modifications : motif obligatoire, journalisé.
    assert (
        api.post(
            f"{E}/lesson-versions/{lv['id']}/request-changes", headers=w.reviewer.h, json={"comment": "x"}
        ).status_code
        == 422
    )
    ok(
        api.post(
            f"{E}/lesson-versions/{lv['id']}/request-changes",
            headers=w.reviewer.h,
            json={"comment": "Préciser la consigne fictive"},
        )
    )
    assert (
        api.delete(
            f"{E}/lesson-versions/{lv['id']}",
            headers={
                **w.author.h,
                "If-Match": api.get(f"{E}/lesson-versions/{lv['id']}", headers=w.author.h).headers["ETag"],
            },
        ).json()["code"]
        == "history"
    )
    ok(submit(api, w.author, "lesson-versions", lv["id"]))
    decided = ok(approve(api, w.reviewer, "lesson-versions", lv["id"]))
    assert decided["status"] == "approved" and decided["decision"]["actor_id"] == w.reviewer.user_id
    history = ok(api.get(f"{E}/lesson-versions/{lv['id']}/decisions", headers=w.author.h))
    assert [d["decision"] for d in history] == ["submitted", "changes_requested", "submitted", "approved"]
    assert history[1]["comment"] == "Préciser la consigne fictive"
    row = sql(sync_engine, "SELECT reviewed_by, content_sha256 FROM lesson_versions WHERE id = :i", i=lv["id"]).one()
    assert str(row.reviewed_by) == w.reviewer.user_id and row.content_sha256


def test_reviewer_must_be_independent_of_included_versions(api: TestClient, w: World) -> None:
    """D12 : le relecteur d'une version de module n'a écrit aucune version de leçon incluse."""
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    lv = ok(new_version(api, w.reviewer2, les["id"]), 201)  # écrite par reviewer2 (aussi formateur)
    ok(submit(api, w.reviewer2, "lesson-versions", lv["id"]))
    ok(approve(api, w.reviewer, "lesson-versions", lv["id"]))
    mv = ok(
        api.post(
            f"{E}/modules/{mod['id']}/versions",
            headers=w.author.h,
            json={"lessons": [{"lesson_id": les["id"], "position": 0, "versions": {"fr": lv["id"]}}]},
        ),
        201,
    )
    ok(submit(api, w.author, "module-versions", mv["id"]))
    r = approve(api, w.reviewer2, "module-versions", mv["id"])
    assert r.status_code == 403 and problem_code(r) == "reviewer_not_independent"
    ok(approve(api, w.reviewer, "module-versions", mv["id"]))


def test_submission_requires_submitted_components(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    lv = ok(new_version(api, w.author, les["id"]), 201)
    mv = ok(
        api.post(
            f"{E}/modules/{mod['id']}/versions",
            headers=w.author.h,
            json={"lessons": [{"lesson_id": les["id"], "position": 0, "versions": {"fr": lv["id"]}}]},
        ),
        201,
    )
    r = submit(api, w.author, "module-versions", mv["id"])
    assert r.status_code == 422 and problem_code(r) == "unsubmitted_component"
    empty = ok(
        api.post(
            f"{E}/lessons/{les['id']}/versions", headers=w.author.h, json={"locale": "en", "title": "Empty lesson"}
        ),
        201,
    )
    r = submit(api, w.author, "lesson-versions", empty["id"])
    assert r.status_code == 422 and problem_code(r) == "empty_content"


def test_abandon_and_delete(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    a = new_version(api, w.author, les["id"])
    assert (
        api.delete(
            f"{E}/lesson-versions/{a.json()['id']}", headers={**w.author.h, "If-Match": a.headers["ETag"]}
        ).status_code
        == 204
    )
    b = ok(new_version(api, w.author, les["id"]), 201)
    assert (
        api.post(f"{E}/lesson-versions/{b['id']}/abandon", headers=w.author.h, json={"reason": "x"}).status_code == 422
    )
    done = ok(
        api.post(
            f"{E}/lesson-versions/{b['id']}/abandon", headers=w.author.h, json={"reason": "Piste abandonnée (test)"}
        )
    )
    assert done["status"] == "archived" and done["decision"]["decision"] == "abandoned"
    assert (
        api.post(
            f"{E}/lesson-versions/{b['id']}/abandon", headers=w.author.h, json={"reason": "Deuxième fois"}
        ).status_code
        == 409
    )


def test_review_queue(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.reviewer2)
    les = new_lesson(api, w.reviewer2, mod["id"])
    lv = ok(new_version(api, w.reviewer2, les["id"]), 201)
    ok(submit(api, w.reviewer2, "lesson-versions", lv["id"]))
    assert api.get(f"{E}/review-queue", headers=w.student.h).status_code == 403
    assert api.get(f"{E}/review-queue", headers=w.author.h).status_code == 403
    mine = {i["id"]: i for i in ok(api.get(f"{E}/review-queue", headers=w.reviewer2.h))}
    theirs = {i["id"]: i for i in ok(api.get(f"{E}/review-queue", headers=w.reviewer.h))}
    assert mine[lv["id"]]["can_review"] is False and theirs[lv["id"]]["can_review"] is True
    assert lv["id"] not in {i["id"] for i in ok(api.get(f"{E}/review-queue", headers=w.org_rev.h))}


def test_org_reviewer_scope(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.org_instr, org=w.org_a)
    les = new_lesson(api, w.org_instr, mod["id"])
    lv = ok(new_version(api, w.org_instr, les["id"]), 201)
    ok(submit(api, w.org_instr, "lesson-versions", lv["id"]))
    assert lv["id"] in {i["id"] for i in ok(api.get(f"{E}/review-queue", headers=w.org_rev.h))}
    ok(approve(api, w.org_rev, "lesson-versions", lv["id"]))


# --------------------------------------------------------------------------------------------
# b4 — sources médicales
# --------------------------------------------------------------------------------------------
def test_sources_registration_verification_and_withdrawal(api: TestClient, w: World, sync_engine: Engine) -> None:
    ref = ok(
        api.post(
            f"{E}/references",
            headers=w.author.h,
            json={"kind": "guideline", "title": f"Source fictive {code()}", "url": "https://example.test/x"},
        ),
        201,
    )
    assert ref["verified"] is False and ref["verified_by"] is None
    assert (
        api.post(
            f"{E}/references",
            headers=w.student.h,
            json={"kind": "guideline", "title": "Source", "url": "https://example.test"},
        ).status_code
        == 403
    )
    assert (
        api.post(
            f"{E}/references", headers=w.author.h, json={"kind": "guideline", "title": "Sans lien vérifiable"}
        ).status_code
        == 422
    )
    assert (
        api.post(
            f"{E}/references",
            headers=w.author.h,
            json={"kind": "guideline", "title": "Lien non sécurisé", "url": "http://example.test"},
        ).status_code
        == 422
    )
    for who in (w.author, w.org_rev):  # formateur ; relecteur d'établissement (non global)
        r = api.post(f"{E}/references/{ref['id']}/verify", headers=who.h)
        assert r.status_code == 403 and problem_code(r) == "reviewer_required"
    assert api.post(f"{E}/references/{ref['id']}/verify", headers=w.student.h).status_code == 404
    done = ok(api.post(f"{E}/references/{ref['id']}/verify", headers=w.reviewer.h))
    assert done["verified"] is True and done["verified_by"] == w.reviewer.user_id
    assert done["last_verified_at"] == date.today().isoformat()
    listed = ok(api.get(f"{E}/references", headers=w.author.h, params={"verified": True, "q": ref["title"]}))
    assert [x["id"] for x in listed] == [ref["id"]]
    ok(api.post(f"{E}/references/{ref['id']}/withdraw", headers=w.reviewer.h, json={"reason": "Remplacée (test)"}))
    assert api.post(f"{E}/references/{ref['id']}/verify", headers=w.reviewer.h).status_code == 409
    # Une source retirée ne peut plus être citée.
    mod = new_module(api, w, w.author)
    r = new_version(api, w.author, new_lesson(api, w.author, mod["id"])["id"])
    put = api.put(
        f"{E}/lesson-versions/{r.json()['id']}/sources",
        headers={**w.author.h, "If-Match": r.headers["ETag"]},
        json={"references": [{"reference_id": ref["id"]}]},
    )
    assert put.status_code == 422


def test_medical_lessons_need_verified_sources(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    body = {"schema": "blocks/v1", "blocks": [{"id": "s1", "type": "steps", "items": ["Étape fictive"]}]}
    r = new_version(api, w.author, les["id"], body)
    lv = ok(r, 201)
    res = submit(api, w.author, "lesson-versions", lv["id"])
    assert res.status_code == 422 and problem_code(res) == "missing_source"
    ref = ok(
        api.post(
            f"{E}/references",
            headers=w.author.h,
            json={"kind": "guideline", "title": f"Source fictive {code()}", "doi": "10.1234/fictif"},
        ),
        201,
    )
    ok(
        api.put(
            f"{E}/lesson-versions/{lv['id']}/sources",
            headers={**w.author.h, "If-Match": r.headers["ETag"]},
            json={"references": [{"reference_id": ref["id"], "locator": "p. 1"}]},
        )
    )
    ok(submit(api, w.author, "lesson-versions", lv["id"]))
    res = approve(api, w.reviewer, "lesson-versions", lv["id"])
    assert res.status_code == 422 and problem_code(res) == "source_unverified"  # source non vérifiée refusée
    ok(api.post(f"{E}/references/{ref['id']}/verify", headers=w.reviewer.h))
    ok(approve(api, w.reviewer, "lesson-versions", lv["id"]))


# --------------------------------------------------------------------------------------------
# b5 — publication contrôlée
# --------------------------------------------------------------------------------------------
def test_non_medical_release_publication_then_archive(api: TestClient, w: World, sync_engine: Engine) -> None:
    chain = approved_chain(api, w)
    rel = chain["release"]
    assert api.post(f"{E}/releases/{rel['id']}/publish", headers=w.author.h).status_code == 403
    assert api.post(f"{E}/releases/{rel['id']}/publish", headers=w.student.h).status_code == 404
    published = ok(api.post(f"{E}/releases/{rel['id']}/publish", headers=w.reviewer.h))
    assert published["status"] == "published" and published["published_by"] == w.reviewer.user_id
    statuses = sql(
        sync_engine,
        "SELECT (SELECT status FROM lesson_versions WHERE id = :lv), "
        "(SELECT status FROM module_versions WHERE id = :mv), (SELECT status FROM programs WHERE id = :p)",
        lv=chain["lv"]["id"],
        mv=chain["mv"]["id"],
        p=chain["program"]["id"],
    ).one()
    assert tuple(statuses) == ("published", "published", "published")
    path = f"{E}/lesson-versions/{chain['lv']['id']}"
    gone = api.delete(path, headers=_etag(api, w.author, path))
    assert gone.status_code == 409 and problem_code(gone) == "not_draft"
    # Une version citée par une édition publiée ne s'archive pas.
    r = api.post(
        f"{E}/module-versions/{chain['mv']['id']}/archive", headers=w.reviewer.h, json={"reason": "Essai d'archivage"}
    )
    assert r.status_code == 409 and problem_code(r) == "in_use"
    assert api.post(f"{E}/releases/{rel['id']}/publish", headers=w.reviewer.h).status_code == 409
    ok(api.post(f"{E}/releases/{rel['id']}/archive", headers=w.reviewer.h, json={"reason": "Fin du test"}))
    assert (
        sql(sync_engine, "SELECT status FROM programs WHERE id = :p", p=chain["program"]["id"]).scalar() == "archived"
    )


def test_medical_publication_is_disabled(api: TestClient, w: World, sync_engine: Engine) -> None:
    chain = approved_chain(api, w, medical=True)
    rel = chain["release"]
    r = api.post(f"{E}/releases/{rel['id']}/publish", headers=w.reviewer.h)
    assert r.status_code == 403 and r.json()["type"].endswith("medical-publication-disabled")
    assert sql(sync_engine, "SELECT status FROM program_releases WHERE id = :r", r=rel["id"]).scalar() == "approved"
    assert sql(sync_engine, "SELECT status FROM programs WHERE id = :p", p=chain["program"]["id"]).scalar() == "draft"
    # Aucun contournement : la nature médicale est figée dès la première édition soumise.
    flip = api.patch(f"{E}/programs/{chain['program']['id']}", headers=w.author.h, json={"is_medical": False})
    assert flip.status_code == 409 and problem_code(flip) == "frozen_program"


def test_publication_checks_refuse_withdrawn_sources(
    api: TestClient, api_flag_on: TestClient, w: World, sync_engine: Engine
) -> None:
    """Même drapeau activé, une édition dont une source a été retirée n'est pas publiable."""
    chain = approved_chain(api, w, medical=True)
    ok(
        api.post(
            f"{E}/references/{chain['ref']['id']}/withdraw",
            headers=w.reviewer.h,
            json={"reason": "Source retirée (test)"},
        )
    )
    reviewer = Acct(
        w.reviewer.email,
        ok(api_flag_on.post("/api/v1/auth/login", json={"email": w.reviewer.email, "password": PASSWORD}))[
            "session_token"
        ],
        w.reviewer.user_id,
    )
    r = api_flag_on.post(f"{E}/releases/{chain['release']['id']}/publish", headers=reviewer.h)
    assert r.status_code == 422 and problem_code(r) == "publication_requirements"
    issues = {i["issue"] for i in r.json()["issues"]}
    assert {"missing_verified_source", "withdrawn_source"} <= issues
    status = sql(sync_engine, "SELECT status FROM program_releases WHERE id = :r", r=chain["release"]["id"]).scalar()
    assert status == "approved"


def test_publication_requires_publisher_in_scope(api: TestClient, w: World) -> None:
    chain = approved_chain(api, w, org=w.org_a, author=w.org_instr, reviewer=w.org_rev)
    rel = chain["release"]
    assert api.post(f"{E}/releases/{rel['id']}/publish", headers=w.org_instr.h).status_code == 403
    assert api.post(f"{E}/releases/{rel['id']}/publish", headers=w.other_instr.h).status_code == 404
    assert api.post(f"{E}/releases/{rel['id']}/publish", headers=w.org_admin.h).status_code == 404


# --------------------------------------------------------------------------------------------
# Audit et erreurs
# --------------------------------------------------------------------------------------------
def test_audit_trail_without_content(api: TestClient, w: World, sync_engine: Engine) -> None:
    secret_text = f"Texte pédagogique fictif {uuid.uuid4().hex}"
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    lv = ok(new_version(api, w.author, les["id"], para(secret_text)), 201)
    ok(submit(api, w.author, "lesson-versions", lv["id"]))
    ok(
        api.post(
            f"{E}/lesson-versions/{lv['id']}/request-changes",
            headers=w.reviewer.h,
            json={"comment": f"Commentaire {secret_text}"},
        )
    )
    rows = sql(
        sync_engine,
        "SELECT action, actor_user_id, actor_role, metadata::text AS m FROM audit_logs "
        "WHERE entity_id = :i ORDER BY id",
        i=lv["id"],
    ).all()
    assert [r.action for r in rows] == ["content.created", "content.submitted", "content.changes_requested"]
    assert [str(r.actor_user_id) for r in rows] == [w.author.user_id, w.author.user_id, w.reviewer.user_id]
    assert rows[2].actor_role == "medical_reviewer"
    assert all(secret_text not in r.m and "body" not in r.m for r in rows)


def test_database_errors_are_stable_problems_without_sql(api: TestClient, w: World) -> None:
    mod = new_module(api, w, w.author)
    new_lesson(api, w.author, mod["id"], 0)
    dup = api.post(f"{E}/modules/{mod['id']}/lessons", headers=w.author.h, json={"code": code(), "position": 0})
    assert dup.status_code == 409 and problem_code(dup) == "23505"
    prog = new_program(api, w, w.author)
    again = api.post(
        f"{E}/programs",
        headers=w.author.h,
        json={"code": prog["code"], "profession_id": w.prof, "title": {"fr": "Doublon"}, "level": "beginner"},
    )
    assert again.status_code == 409 and problem_code(again) == "23505"
    unknown = api.post(
        f"{E}/programs",
        headers=w.author.h,
        json={
            "code": code(),
            "profession_id": str(uuid.uuid4()),
            "title": {"fr": "Métier inconnu"},
            "level": "beginner",
        },
    )
    assert unknown.status_code == 422 and problem_code(unknown) == "23503"


def test_error_translation_codes() -> None:
    assert code_for("23514", "nl:in_use", "peu importe") == "in_use"
    assert code_for("42501", None, "Auto-approbation interdite : x") == "self_approval"
    assert code_for("42501", None, "message inconnu") == "forbidden"
    assert code_for("23505", None, "duplicate") is None
    assert all(c in CODES for _, c in LEGACY_MESSAGES)


def _raise_messages(sql_text: str) -> list[tuple[str, bool]]:
    found = []
    for m in re.finditer(r"RAISE EXCEPTION '((?:[^']|'')*)'(.*?);", sql_text, re.S):
        found.append((m.group(1).replace("''", "'"), "HINT" in m.group(2)))
    return found


def test_every_editorial_database_rule_has_a_stable_code() -> None:
    """Couverture de la liste fermée : toute règle éditoriale de 0005/0006 a un code stable."""
    s5 = (SCHEMA_DIR / "0005_learning_engine.sql").read_text(encoding="utf-8")
    s6 = (SCHEMA_DIR / "0006_independent_program_versions.sql").read_text(encoding="utf-8")
    editorial_5 = s5[: s5.index("-- 6. Inscriptions")]
    messages = [m for m, _ in _raise_messages(editorial_5)]
    learner_rules = ("nscription", "Cohorte", "étudiant", "Changement d'édition")  # lot inscriptions, hors API
    messages += [m for m, hinted in _raise_messages(s6) if not hinted and not any(k in m for k in learner_rules)]
    assert messages
    missing = [m for m in messages if code_for("23514", None, m) is None]
    assert missing == []
    hints = set(re.findall(r"HINT = 'nl:([a-z_]+)'", s6))
    editorial_hints = hints - {
        "incoherent_event",
        "incoherent_progress",
        "incoherent_resume_point",
        "inactive_enrollment",
    }
    assert editorial_hints <= set(CODES), sorted(editorial_hints - set(CODES))


def _etag(api: TestClient, who: Acct, path: str) -> dict[str, str]:
    return {**who.h, "If-Match": api.get(path, headers=who.h).headers["ETag"]}


def test_delete_never_removes_submitted_referenced_or_historical_data(
    api: TestClient, w: World, sync_engine: Engine
) -> None:
    """DELETE : seulement un brouillon jamais soumis, non référencé, sans trace ; audité ; aucune cascade."""
    mod = new_module(api, w, w.author)
    les = new_lesson(api, w.author, mod["id"])
    # Soumis (medical_review) : refus.
    submitted = ok(new_version(api, w.author, les["id"]), 201)
    ok(submit(api, w.author, "lesson-versions", submitted["id"]))
    path = f"{E}/lesson-versions/{submitted['id']}"
    r = api.delete(path, headers=_etag(api, w.author, path))
    assert r.status_code == 409 and problem_code(r) == "not_draft"
    # Brouillon cité par une version de module, et version de module citée par une édition : refus.
    cited = ok(new_version(api, w.author, new_lesson(api, w.author, mod["id"], 1)["id"]), 201)
    mv = ok(
        api.post(
            f"{E}/modules/{mod['id']}/versions",
            headers=w.author.h,
            json={"lessons": [{"lesson_id": cited["lesson_id"], "position": 0, "versions": {"fr": cited["id"]}}]},
        ),
        201,
    )
    path = f"{E}/lesson-versions/{cited['id']}"
    r = api.delete(path, headers=_etag(api, w.author, path))
    assert r.status_code == 409 and problem_code(r) == "in_use"
    prog = new_program(api, w, w.author)
    ok(
        api.post(
            f"{E}/programs/{prog['id']}/releases",
            headers=w.author.h,
            json={"modules": [{"module_version_id": mv["id"], "position": 0}], "change_summary": "Brouillon"},
        ),
        201,
    )
    path = f"{E}/module-versions/{mv['id']}"
    r = api.delete(path, headers=_etag(api, w.author, path))
    assert r.status_code == 409 and problem_code(r) == "in_use"
    # Brouillon visé par un signalement (clé étrangère en CASCADE) : refus, le signalement subsiste.
    reported = ok(new_version(api, w.author, new_lesson(api, w.author, mod["id"], 2)["id"]), 201)
    report = sql(
        sync_engine,
        "INSERT INTO content_issue_reports (lesson_version_id, category, description) "
        "VALUES (:v, 'typo', 'Signalement fictif (test)') RETURNING id",
        v=reported["id"],
    ).scalar()
    path = f"{E}/lesson-versions/{reported['id']}"
    r = api.delete(path, headers=_etag(api, w.author, path))
    assert r.status_code == 409 and problem_code(r) == "history"
    assert sql(sync_engine, "SELECT count(*) FROM content_issue_reports WHERE id = :i", i=report).scalar() == 1
    # Brouillon libre : suppression autorisée, auditée, sans toucher à la source citée.
    free = new_version(api, w.author, new_lesson(api, w.author, mod["id"], 3)["id"])
    ref = ok(
        api.post(
            f"{E}/references",
            headers=w.author.h,
            json={"kind": "guideline", "title": f"Source fictive {code()}", "url": "https://example.test/d"},
        ),
        201,
    )
    put = api.put(
        f"{E}/lesson-versions/{free.json()['id']}/sources",
        headers={**w.author.h, "If-Match": free.headers["ETag"]},
        json={"references": [{"reference_id": ref["id"]}]},
    )
    path = f"{E}/lesson-versions/{free.json()['id']}"
    assert api.delete(path, headers={**w.author.h, "If-Match": put.headers["ETag"]}).status_code == 204
    assert sql(sync_engine, "SELECT count(*) FROM medical_references WHERE id = :i", i=ref["id"]).scalar() == 1
    audit = sql(
        sync_engine,
        "SELECT action, actor_user_id FROM audit_logs WHERE entity_id = :i ORDER BY id DESC LIMIT 1",
        i=free.json()["id"],
    ).one()
    assert audit.action == "content.deleted" and str(audit.actor_user_id) == w.author.user_id
    # Aucune route ne supprime une décision ou un historique d'apprentissage.
    paths = api.app.openapi()["paths"]  # type: ignore[attr-defined]
    deletable = sorted(p for p, item in paths.items() if p.startswith(E) and "delete" in item)
    assert deletable == [f"{E}/lesson-versions/{{version_id}}", f"{E}/module-versions/{{version_id}}"]

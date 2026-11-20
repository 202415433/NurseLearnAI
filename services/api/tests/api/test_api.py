"""Tests d'intégration de l'API (phase 1.2) sur PostgreSQL et Redis réels."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.db.models import Base
from app.main import create_app

PROBLEM = "application/problem+json"


def codes(page: dict) -> list[str]:
    return [item["code"] for item in page["items"]]


# --------------------------------------------------------------------------- santé et transversal
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert len(r.headers["X-Request-ID"]) >= 8


def test_ready_checks_database_and_redis(client):
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert checks["database"] == "ok"
    assert checks["redis"] == ("ok" if os.environ.get("REDIS_URL") else "not_configured")


def test_security_headers_and_request_id_propagation(client):
    r = client.get("/health", headers={"X-Request-ID": "abcdef0123456789"})
    assert r.headers["X-Request-ID"] == "abcdef0123456789"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"
    # Un identifiant fourni malformé est remplacé, jamais recopié.
    assert client.get("/health", headers={"X-Request-ID": "<script>"}).headers["X-Request-ID"] != "<script>"


def test_openapi_lists_phase_1_2_routes_read_only(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/countries",
        "/api/v1/jurisdictions",
        "/api/v1/professions",
        "/api/v1/specialties",
        "/api/v1/programs",
        "/api/v1/programs/{program_id}",
        "/health",
        "/ready",
    ):
        assert p in paths, p
    # Référentiels et catalogue restent en lecture seule ; les écritures n'existent que pour l'identité (2.1).
    writable = ("/api/v1/auth/", "/api/v1/me", "/api/v1/invitations", "/api/v1/organizations/", "/api/v1/admin/")
    for path, item in paths.items():
        if not path.startswith(writable):
            assert set(item) == {"get"}, f"{path} doit rester en lecture seule"
    assert client.post("/api/v1/programs").status_code == 405


def test_about_states_training_is_not_a_diploma(client):
    d = client.get("/api/v1/about").json()["disclaimer"]
    assert "ni un diplôme" in d["fr"] and "not a diploma" in d["en"]


# --------------------------------------------------------------------------- pays et juridictions
def test_countries(client):
    page = client.get("/api/v1/countries").json()
    by_code = {c["code"]: c for c in page["items"]}
    assert {"CA", "FR", "SN"} <= set(by_code)
    assert by_code["CA"]["jurisdiction_count"] == 5
    assert by_code["SN"]["name"] == "Sénégal"
    en = {c["code"]: c for c in client.get("/api/v1/countries", headers={"Accept-Language": "en"}).json()["items"]}
    assert en["SN"]["name"] == "Senegal"


def test_jurisdictions_filtered_by_country_and_scope(client):
    assert set(codes(client.get("/api/v1/jurisdictions", params={"country": "CA"}).json())) == {
        "CA",
        "CA-QC",
        "CA-ON",
        "CA-BC",
        "CA-AB",
    }
    assert codes(client.get("/api/v1/jurisdictions", params={"country": "fr"}).json()) == ["FR"]
    assert codes(client.get("/api/v1/jurisdictions", params={"country": "SN"}).json()) == ["SN"]
    assert set(codes(client.get("/api/v1/jurisdictions", params={"scope": "framework"}).json())) == {
        "FRANCOPHONIE",
        "INTL",
    }
    qc = next(
        j for j in client.get("/api/v1/jurisdictions", params={"country": "CA"}).json()["items"] if j["code"] == "CA-QC"
    )
    assert qc["name"] == "Québec" and qc["parent_code"] == "CA"


def test_pagination(client):
    full = client.get("/api/v1/jurisdictions").json()
    assert full["total"] == 9 and full["limit"] == 50 and full["offset"] == 0
    first = client.get("/api/v1/jurisdictions", params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["total"] == 9
    last = client.get("/api/v1/jurisdictions", params={"limit": 2, "offset": 8}).json()
    assert len(last["items"]) == 1
    rest = client.get("/api/v1/jurisdictions", params={"limit": 7, "offset": 2}).json()
    assert codes(first) + codes(rest) == codes(full)


@pytest.mark.parametrize(
    "path,params",
    [
        ("/api/v1/jurisdictions", {"country": "CAN"}),
        ("/api/v1/jurisdictions", {"scope": "galaxy"}),
        ("/api/v1/jurisdictions", {"limit": 0}),
        ("/api/v1/jurisdictions", {"limit": 101}),
        ("/api/v1/jurisdictions", {"offset": -1}),
        ("/api/v1/professions", {"jurisdiction": "QC;DROP"}),
        ("/api/v1/professions", {"category": "doctor"}),
        ("/api/v1/specialties", {"group": "Z"}),
        ("/api/v1/programs", {"level": "expert"}),
        ("/api/v1/programs", {"specialty": "../etc"}),
    ],
)
def test_strict_input_validation(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 422, (path, params, r.text)
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["errors"]


# --------------------------------------------------------------------------- métiers
def test_professions_are_scoped_to_their_jurisdiction(client):
    qc = client.get("/api/v1/professions", params={"jurisdiction": "CA-QC"}).json()
    assert codes(qc) == ["pab", "infirmiere_auxiliaire", "infirmiere"]
    assert {p["jurisdiction_code"] for p in qc["items"]} == {"CA-QC"}
    assert {p["validation_status"] for p in qc["items"]} == {"pending_expert_review"}
    assert codes(client.get("/api/v1/professions", params={"jurisdiction": "CA-ON"}).json()) == ["psw", "rpn", "rn"]
    fr = codes(client.get("/api/v1/professions", params={"jurisdiction": "FR"}).json())
    assert fr == ["aide_soignant", "infirmier"] and "pab" not in fr, "aucune équivalence implicite"
    assert codes(client.get("/api/v1/professions", params={"jurisdiction": "SN"}).json()) == [
        "aide_infirmier",
        "infirmier_etat",
    ]
    assert client.get("/api/v1/professions", params={"jurisdiction": "XX-ZZ"}).json()["total"] == 0
    regulated = client.get(
        "/api/v1/professions", params={"jurisdiction": "CA-QC", "category": "registered_nurse"}
    ).json()
    assert codes(regulated) == ["infirmiere"] and regulated["items"][0]["is_regulated"] is True


def test_availability_exposes_counts_only(client):
    pab = client.get("/api/v1/professions", params={"jurisdiction": "CA-QC"}).json()["items"][0]
    assert pab["code"] == "pab"
    assert pab["availability"] == {"published": 0, "in_preparation": 1}  # pilote en brouillon
    assert "Hygiène" not in str(pab), "aucun détail de brouillon n'est exposé"


# --------------------------------------------------------------------------- spécialités
def test_specialties(client):
    page = client.get("/api/v1/specialties").json()
    assert page["total"] == 27
    assert {s["group"] for s in page["items"]} == set("ABCDEFGH")
    assert client.get("/api/v1/specialties", params={"group": "h"}).json()["total"] == 20
    assert all(s["published_programs"] == 0 for s in page["items"])


# --------------------------------------------------------------------------- programmes : visibilité
def test_pilot_draft_is_never_exposed(client):
    assert client.get("/api/v1/programs").json()["total"] == 0
    assert client.get("/api/v1/programs", params={"jurisdiction": "CA-QC", "profession": "pab"}).json()["items"] == []
    missing = client.get(f"/api/v1/programs/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.headers["content-type"].startswith(PROBLEM)
    assert client.get("/api/v1/programs/pas-un-uuid").status_code == 422


def test_draft_program_detail_is_404(client, sync_engine):
    with sync_engine.connect() as c:
        pilot_id = c.execute(text("SELECT id FROM programs WHERE code = 'pab-qc-socle'")).scalar()
    assert pilot_id is not None
    assert client.get(f"/api/v1/programs/{pilot_id}").status_code == 404


def test_published_content_and_jurisdiction_filters(client, published_demo):
    params = {"jurisdiction": "CA-QC", "profession": "pab", "specialty": "care_assistant_core"}
    page = client.get("/api/v1/programs", params=params).json()
    assert codes(page) == [published_demo["program"]]
    item = page["items"][0]
    assert item["module_count"] == 1 and item["profession_title"].startswith("Préposé")
    assert client.get("/api/v1/programs", params={**params, "jurisdiction": "CA-ON"}).json()["total"] == 0
    assert client.get("/api/v1/programs", params={"jurisdiction": "FR"}).json()["total"] == 0

    detail = client.get(f"/api/v1/programs/{item['id']}").json()
    assert [m["code"] for m in detail["modules"]] == [published_demo["module"]]
    lessons = detail["modules"][0]["lessons"]
    assert [lesson["code"] for lesson in lessons] == ["publiee"], (
        "une leçon sans version publiée ne doit pas apparaître"
    )
    # Version propre à la juridiction du programme (Québec) préférée au contenu commun.
    assert lessons[0]["title"] == "Leçon Québec (test)" and lessons[0]["jurisdiction_specific"] is True
    en = client.get(f"/api/v1/programs/{item['id']}", headers={"Accept-Language": "en"}).json()
    assert en["title"] == "Demo program (test)" and en["modules"][0]["lessons"][0]["title"] == "Common lesson (test)"

    pab = client.get("/api/v1/professions", params={"jurisdiction": "CA-QC"}).json()["items"][0]
    assert pab["availability"] == {"published": 1, "in_preparation": 1}
    specialties = client.get("/api/v1/specialties", params={"jurisdiction": "CA-QC", "profession": "pab"}).json()[
        "items"
    ]
    assert specialties[0]["code"] == "care_assistant_core" and specialties[0]["published_programs"] == 1


def test_organization_content_is_isolated(client, published_demo, sync_engine):
    every = client.get("/api/v1/programs", params={"limit": 100}).json()
    assert published_demo["private"] not in codes(every)
    with sync_engine.connect() as c:
        private_id = c.execute(
            text("SELECT id FROM programs WHERE code = :c"), {"c": published_demo["private"]}
        ).scalar()
    assert client.get(f"/api/v1/programs/{private_id}").status_code == 404
    # Les compteurs publics n'incluent pas le programme d'établissement.
    pab = client.get("/api/v1/professions", params={"jurisdiction": "CA-QC"}).json()["items"][0]
    assert pab["availability"]["published"] == 1
    for path in ("/api/v1/programs", "/api/v1/professions", "/api/v1/specialties", "/api/v1/jurisdictions"):
        assert "Programme privé" not in client.get(path, params={"limit": 100}).text


# --------------------------------------------------------------------------- sécurité
def test_no_secrets_or_internals_in_responses(client, settings):
    password = settings.database_url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[-1]
    bodies = [
        client.get(p).text
        for p in (
            "/ready",
            "/api/v1/countries",
            "/api/v1/programs/x",
            f"/api/v1/programs/{uuid.uuid4()}",
            "/api/v1/jurisdictions?limit=0",
        )
    ]
    for body in bodies:
        if len(password) >= 8:  # un mot de passe trop court (CI) produirait des faux positifs
            assert password not in body
        assert "postgresql" not in body and "Traceback" not in body and "sqlalchemy" not in body.lower()


def test_unexpected_errors_are_generic(settings):
    app = create_app(settings)

    @app.get("/api/v1/_boom", include_in_schema=False)
    async def boom() -> None:
        raise RuntimeError("secret interne : mot-de-passe-xyz")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/_boom")
    assert r.status_code == 500 and r.headers["content-type"].startswith(PROBLEM)
    assert "mot-de-passe-xyz" not in r.text and "RuntimeError" not in r.text


@pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="Redis requis pour la limitation de débit")
def test_rate_limit(settings):
    # Sel propre au test : compteurs indépendants de ceux des autres tests.
    limited = Settings(cors_origins=settings.cors_origins, rate_limit_per_minute=3, rate_limit_salt=uuid.uuid4().hex)  # type: ignore[call-arg]
    with TestClient(create_app(limited)) as c:
        statuses = [c.get("/api/v1/countries").status_code for _ in range(5)]
        last = c.get("/api/v1/countries")
    assert statuses[:3] == [200, 200, 200] and 429 in statuses
    assert last.status_code == 429 and int(last.headers["Retry-After"]) >= 0
    assert last.headers["content-type"].startswith(PROBLEM)
    assert last.headers["RateLimit-Limit"] == "3" and last.headers["RateLimit-Remaining"] == "0"


def test_rate_limit_per_visitor_behind_trusted_proxy(settings):
    """Derrière un mandataire approuvé, chaque visiteur a son propre compteur (X-Forwarded-For)."""
    cfg = Settings(  # type: ignore[call-arg]
        cors_origins=settings.cors_origins,
        rate_limit_per_minute=2,
        rate_limit_salt=uuid.uuid4().hex,
        trusted_proxies=["testclient"],
    )
    with TestClient(create_app(cfg)) as c:
        a = [c.get("/api/v1/countries", headers={"X-Forwarded-For": "203.0.113.10"}).status_code for _ in range(3)]
        b = c.get("/api/v1/countries", headers={"X-Forwarded-For": "203.0.113.20"}).status_code
    assert a == [200, 200, 429]
    assert b == 200


def test_forwarded_for_ignored_from_untrusted_peer(settings):
    """Sans mandataire approuvé, X-Forwarded-For est ignoré : il ne permet pas de contourner la limite."""
    cfg = Settings(cors_origins=settings.cors_origins, rate_limit_per_minute=2, rate_limit_salt=uuid.uuid4().hex)  # type: ignore[call-arg]
    with TestClient(create_app(cfg)) as c:
        statuses = [
            c.get("/api/v1/countries", headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(3)
        ]
    assert statuses == [200, 200, 429]


def test_security_headers_and_csp(client):
    r = client.get("/api/v1/countries")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    # La documentation interactive garde ses ressources (pas de CSP restrictive).
    assert "Content-Security-Policy" not in client.get("/docs").headers


def test_unknown_route_and_method_are_generic(client):
    r = client.get("/api/v1/inexistant")
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)
    body = r.text
    assert "Traceback" not in body and "starlette" not in body.lower()
    m = client.delete("/api/v1/countries")
    assert m.status_code == 405 and m.headers["content-type"].startswith(PROBLEM)
    assert "GET" in m.headers.get("allow", "")


def test_docs_hidden_in_production(settings):
    prod = Settings(  # type: ignore[call-arg]
        app_env="production", cors_origins=["https://nurselearn.example"], token_hash_key="k" * 40
    )
    with TestClient(create_app(prod)) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/health").status_code == 200


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValueError):
        Settings(  # type: ignore[call-arg]
            app_env="production", cors_origins=["*"], database_url="postgresql://x@y/z", token_hash_key="k" * 40
        )


def test_production_requires_token_hash_key():
    with pytest.raises(ValueError):
        Settings(app_env="production", cors_origins=["https://x.example"], database_url="postgresql://x@y/z")  # type: ignore[call-arg]


def test_cors_allows_only_configured_origin(client):
    ok = client.options(
        "/api/v1/countries", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options(
        "/api/v1/countries", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert bad.headers.get("access-control-allow-origin") is None


# --------------------------------------------------------------------------- modèles ↔ schéma
_TYPE_EQUIV = {
    "TEXT": "text",
    "STRING": "character",
    "UUID": "uuid",
    "JSONB": "jsonb",
    "BOOLEAN": "boolean",
    "INTEGER": "integer",
    "DATE": "date",
    "DATETIME": "timestamp with time zone",
}


def test_models_match_schema(sync_engine):
    with sync_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        ).all()
    db = {(t, col): (dt, nullable == "YES") for t, col, dt, nullable in rows}
    problems = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            key = (table.name, column.name)
            if key not in db:
                problems.append(f"{key} absente en base")
                continue
            db_type, db_nullable = db[key]
            expected = _TYPE_EQUIV.get(type(column.type).__name__.upper())
            if expected and not db_type.startswith(expected):
                problems.append(f"{key} : modèle {type(column.type).__name__}, base {db_type}")
            if not column.primary_key and column.nullable != db_nullable:
                problems.append(f"{key} : nullabilité modèle {column.nullable}, base {db_nullable}")
    assert not problems, "\n".join(problems)

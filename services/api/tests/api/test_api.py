"""Tests de bout en bout de l'API (lot 1.2) sur PostgreSQL réel."""
from __future__ import annotations

from sqlalchemy import text

from app.db.models import Base

PROBLEM = "application/problem+json"


# --------------------------------------------------------------------------- santé
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert len(r.headers["X-Request-ID"]) >= 8


def test_ready_checks_database_and_redis(client):
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert checks["database"] == "ok"
    assert checks["redis"] in ("ok", "not_configured")


def test_security_headers_and_request_id_propagation(client):
    r = client.get("/health", headers={"X-Request-ID": "abcdef0123456789"})
    assert r.headers["X-Request-ID"] == "abcdef0123456789"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_openapi_documents_lot_1_2_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/v1/jurisdictions", "/api/v1/jurisdictions/{code}/professions", "/api/v1/specialties",
              "/api/v1/programs", "/api/v1/programs/{code}", "/api/v1/modules/{code}", "/health", "/ready"):
        assert p in paths, p
    assert not any(m != "get" for item in paths.values() for m in item), "le lot 1.2 n'expose que de la lecture"


def test_about_states_training_is_not_a_diploma(client):
    d = client.get("/api/v1/about").json()["disclaimer"]
    assert "ni un diplôme" in d["fr"] and "not a diploma" in d["en"]


# --------------------------------------------------------------------------- référentiels
def test_jurisdictions_localized(client):
    fr = {j["code"]: j for j in client.get("/api/v1/jurisdictions", headers={"Accept-Language": "fr-CA,fr;q=0.9"}).json()}
    en = {j["code"]: j for j in client.get("/api/v1/jurisdictions", headers={"Accept-Language": "en"}).json()}
    assert fr["CA-QC"]["name"] == "Québec" and en["CA-QC"]["name"] == "Quebec"
    assert fr["CA-QC"]["parent_code"] == "CA"
    assert {"CA-QC", "CA-ON", "FR", "SN", "INTL"} <= set(fr)
    r = client.get("/api/v1/jurisdictions?lang=en")
    assert r.headers["Content-Language"] == "en"


def test_professions_are_scoped_to_their_jurisdiction(client):
    r = client.get("/api/v1/jurisdictions/CA-QC/professions")
    assert r.status_code == 200
    items = r.json()
    assert [p["code"] for p in items] == ["pab", "infirmiere_auxiliaire", "infirmiere"]
    assert all(p["jurisdiction_code"] == "CA-QC" for p in items)
    # Données de départ non validées : l'interface doit pouvoir l'afficher.
    assert {p["validation_status"] for p in items} == {"pending_expert_review"}
    fr_codes = {p["code"] for p in client.get("/api/v1/jurisdictions/FR/professions").json()}
    assert "pab" not in fr_codes and "aide_soignant" in fr_codes


def test_unknown_jurisdiction_returns_problem_json(client):
    r = client.get("/api/v1/jurisdictions/XX-ZZ/professions")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["status"] == 404 and body["instance"] == "/api/v1/jurisdictions/XX-ZZ/professions"
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_invalid_parameters_return_422_problem(client):
    r = client.get("/api/v1/programs", params={"level": "expert"})
    assert r.status_code == 422 and r.headers["content-type"].startswith(PROBLEM)
    assert client.get("/api/v1/programs", params={"limit": 1000}).status_code == 422


def test_specialties(client):
    items = client.get("/api/v1/specialties").json()
    assert len(items) == 27
    assert {s["group"] for s in items} == set("ABCDEFGH")


# --------------------------------------------------------------------------- catalogue
def test_pilot_draft_is_never_exposed(client):
    assert all(p["code"] != "pab-qc-socle" for p in client.get("/api/v1/programs").json())
    assert client.get("/api/v1/programs/pab-qc-socle").status_code == 404
    assert client.get("/api/v1/modules/hygiene-mains-prevention-infections").status_code == 404


def test_published_content_path(client, published_demo):
    listed = {p["code"] for p in client.get("/api/v1/programs", params={"jurisdiction": "CA-QC", "profession": "pab"}).json()}
    assert published_demo["program"] in listed
    assert published_demo["private"] not in listed, "un programme d'établissement ne doit pas être public"
    assert client.get(f"/api/v1/programs/{published_demo['private']}").status_code == 404

    detail = client.get(f"/api/v1/programs/{published_demo['program']}").json()
    assert [m["code"] for m in detail["modules"]] == [published_demo["module"]]

    module = client.get(f"/api/v1/modules/{published_demo['module']}").json()
    assert [l["code"] for l in module["lessons"]] == ["publiee"], "une leçon sans version publiée ne doit pas apparaître"
    assert module["lessons"][0]["title"] == "Leçon commune (test)"
    assert module["lessons"][0]["next_review_due"]

    en = client.get(f"/api/v1/modules/{published_demo['module']}", headers={"Accept-Language": "en"}).json()
    assert en["lessons"][0]["title"] == "Common lesson (test)" and en["title"] == "Demo module (test)"

    qc = client.get(f"/api/v1/modules/{published_demo['module']}", params={"jurisdiction": "CA-QC"}).json()
    assert qc["lessons"][0]["title"] == "Leçon Québec (test)" and qc["lessons"][0]["jurisdiction_specific"] is True


# --------------------------------------------------------------------------- modèles ↔ schéma
_TYPE_EQUIV = {"TEXT": "text", "STRING": "character", "UUID": "uuid", "JSONB": "jsonb", "BOOLEAN": "boolean",
               "INTEGER": "integer", "DATE": "date", "DATETIME": "timestamp with time zone"}


def test_models_match_schema(sync_engine):
    with sync_engine.connect() as c:
        rows = c.execute(text("SELECT table_name, column_name, data_type, is_nullable "
                              "FROM information_schema.columns WHERE table_schema = 'public'")).all()
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

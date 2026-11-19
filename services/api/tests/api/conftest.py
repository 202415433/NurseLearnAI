"""Tests de l'API contre une vraie base migrée (alembic upgrade head) et alimentée (python -m app.seed).

Variables : DATABASE_URL (obligatoire), REDIS_URL (facultatif). Voir le job CI « api ».
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL absent : tests d'API non exécutés", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    # Sel aléatoire : les compteurs de limitation de débit des tests ne se mélangent avec aucun autre.
    return Settings(cors_origins=["http://localhost:3000"], rate_limit_salt=uuid.uuid4().hex)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="session")
def sync_engine(settings: Settings) -> Iterator[Engine]:
    engine = create_engine(settings.sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def published_demo(sync_engine: Engine) -> Iterator[dict[str, str]]:
    """Contenu PUBLIÉ fictif et non médical, créé puis supprimé, pour éprouver le chemin de publication.

    Il passe par toutes les règles de la base : relecteur habilité (rôle medical_reviewer),
    distinct de l'auteur, date de relecture, date de prochaine révision.
    """
    tag = uuid.uuid4().hex[:8]
    ids = {"program": f"test-prog-{tag}", "module": f"test-mod-{tag}", "private": f"test-priv-{tag}"}
    with sync_engine.begin() as c:
        author = c.execute(
            text("INSERT INTO users (email, status, email_verified_at) VALUES (:e, 'active', now()) RETURNING id"),
            {"e": f"author-{tag}@example.test"},
        ).scalar()
        reviewer = c.execute(
            text("INSERT INTO users (email, status, email_verified_at) VALUES (:e, 'active', now()) RETURNING id"),
            {"e": f"rev-{tag}@example.test"},
        ).scalar()
        c.execute(
            text("INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE code='medical_reviewer'"),
            {"u": reviewer},
        )
        org = c.execute(
            text("INSERT INTO organizations (slug, name, kind) VALUES (:s, 'Institut test', 'school') RETURNING id"),
            {"s": f"org-{tag}"},
        ).scalar()
        prof = c.execute(
            text(
                "SELECT p.id FROM professions p JOIN jurisdictions j ON j.id = p.jurisdiction_id "
                "WHERE j.code='CA-QC' AND p.code='pab'"
            )
        ).scalar()
        spec = c.execute(text("SELECT id FROM specialties WHERE code='care_assistant_core'")).scalar()
        qc = c.execute(text("SELECT id FROM jurisdictions WHERE code='CA-QC'")).scalar()
        prog = c.execute(
            text(
                "INSERT INTO programs (code, profession_id, specialty_id, title_i18n, level, access_tier, status) "
                "VALUES (:c, :p, :s, CAST(:t AS jsonb), 'beginner', 'free', 'published') RETURNING id"
            ),
            {
                "c": ids["program"],
                "p": prof,
                "s": spec,
                "t": '{"fr":"Programme de démonstration (test)","en":"Demo program (test)"}',
            },
        ).scalar()
        c.execute(
            text(
                "INSERT INTO programs (code, profession_id, organization_id, title_i18n, level, access_tier, status) "
                "VALUES (:c, :p, :o, CAST(:t AS jsonb), 'beginner', 'organization', 'published')"
            ),
            {"c": ids["private"], "p": prof, "o": org, "t": '{"fr":"Programme privé (test)"}'},
        )
        mod = c.execute(
            text(
                "INSERT INTO modules (code, specialty_id, level, title_i18n, estimated_minutes, access_tier, status) "
                "VALUES (:c, :s, 'beginner', CAST(:t AS jsonb), 15, 'free', 'published') RETURNING id"
            ),
            {"c": ids["module"], "s": spec, "t": '{"fr":"Module de démonstration (test)","en":"Demo module (test)"}'},
        ).scalar()
        c.execute(
            text("INSERT INTO program_modules (program_id, module_id, position) VALUES (:p, :m, 0)"),
            {"p": prog, "m": mod},
        )
        lessons = []
        for pos, code in enumerate(("publiee", "brouillon")):
            lessons.append(
                c.execute(
                    text(
                        "INSERT INTO lessons (module_id, code, position, kind) VALUES (:m, :c, :p, 'theory') RETURNING id"
                    ),
                    {"m": mod, "c": code, "p": pos},
                ).scalar()
            )
        version = (
            "INSERT INTO lesson_versions (lesson_id, locale, jurisdiction_id, version_no, title, body, status, "
            "author_id, reviewed_by, reviewed_at, published_at, next_review_due) VALUES "
            "(:l, :loc, :j, 1, :t, CAST('{\"blocks\":[]}' AS jsonb), 'published', :a, :r, now(), now(), "
            "current_date + 365)"
        )
        c.execute(
            text(version),
            {"l": lessons[0], "loc": "fr", "j": None, "t": "Leçon commune (test)", "a": author, "r": reviewer},
        )
        c.execute(
            text(version),
            {"l": lessons[0], "loc": "en", "j": None, "t": "Common lesson (test)", "a": author, "r": reviewer},
        )
        c.execute(
            text(version),
            {"l": lessons[0], "loc": "fr", "j": qc, "t": "Leçon Québec (test)", "a": author, "r": reviewer},
        )
        c.execute(
            text(
                "INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body) "
                "VALUES (:l, 'fr', 1, 'Brouillon (test)', CAST('{\"blocks\":[]}' AS jsonb))"
            ),
            {"l": lessons[1]},
        )
    yield ids
    with sync_engine.begin() as c:
        c.execute(text("DELETE FROM programs WHERE code IN (:a, :b)"), {"a": ids["program"], "b": ids["private"]})
        c.execute(text("DELETE FROM modules WHERE code = :m"), {"m": ids["module"]})
        c.execute(text("DELETE FROM organizations WHERE slug = :s"), {"s": f"org-{tag}"})
        c.execute(text("DELETE FROM users WHERE email LIKE :e"), {"e": f"%-{tag}@example.test"})

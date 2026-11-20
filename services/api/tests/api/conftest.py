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
    """Contenu PUBLIÉ fictif et non médical, mené par le workflow éditorial complet, puis archivé.

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
        # Données de test : garde d'attribution des rôles (0004) contournée pour cette transaction.
        c.execute(text("SELECT set_config('nl.role_grant_bypass', 'on', true)"))
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
        # Depuis 0005, rien ne s'insère « publié » : contenus créés en brouillon puis menés par le
        # workflow éditorial (soumission par l'auteur, approbation et publication par le relecteur).
        c.execute(text("SELECT set_config('nl.role_grant_bypass', 'on', true)"))
        c.execute(
            text("INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE code='instructor'"),
            {"u": author},
        )
        prog = c.execute(
            text(
                "INSERT INTO programs (code, profession_id, specialty_id, title_i18n, level, access_tier, is_medical) "
                "VALUES (:c, :p, :s, CAST(:t AS jsonb), 'beginner', 'free', false) RETURNING id"
            ),
            {
                "c": ids["program"],
                "p": prof,
                "s": spec,
                "t": '{"fr":"Programme de démonstration (test)","en":"Demo program (test)"}',
            },
        ).scalar()
        private = c.execute(
            text(
                "INSERT INTO programs (code, profession_id, organization_id, title_i18n, level, access_tier, "
                "is_medical) VALUES (:c, :p, :o, CAST(:t AS jsonb), 'beginner', 'organization', false) RETURNING id"
            ),
            {"c": ids["private"], "p": prof, "o": org, "t": '{"fr":"Programme privé (test)"}'},
        ).scalar()
        mod = c.execute(
            text(
                "INSERT INTO modules (code, specialty_id, level, title_i18n, estimated_minutes, access_tier) "
                "VALUES (:c, :s, 'beginner', CAST(:t AS jsonb), 15, 'free') RETURNING id"
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
        body = '{"blocks":[{"id":"b1","type":"paragraph","text":"Contenu fictif non médical"}]}'
        version = (
            "INSERT INTO lesson_versions (lesson_id, locale, jurisdiction_id, version_no, title, body, author_id) "
            "VALUES (:l, :loc, :j, 1, :t, CAST(:b AS jsonb), :a) RETURNING id"
        )
        versions = {}
        for key, loc, jur, title in (
            ("fr", "fr", None, "Leçon commune (test)"),
            ("en", "en", None, "Common lesson (test)"),
            ("qc", "fr", qc, "Leçon Québec (test)"),
        ):
            versions[key] = c.execute(
                text(version), {"l": lessons[0], "loc": loc, "j": jur, "t": title, "b": body, "a": author}
            ).scalar()

        def act(actor: object, sql: str, params: dict) -> None:
            c.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": str(actor)})
            c.execute(text(sql), params)

        for vid in versions.values():
            act(author, "UPDATE lesson_versions SET status = 'medical_review' WHERE id = :v", {"v": vid})
            act(
                reviewer,
                "UPDATE lesson_versions SET status = 'approved', next_review_due = current_date + 365 WHERE id = :v",
                {"v": vid},
            )

        # Une version de module par programme (0006, ADR-0010) : le programme public (Québec) cite la
        # version propre à la juridiction, le programme privé la version commune.
        releases = []
        for program, fr in ((prog, versions["qc"]), (private, versions["fr"])):
            mv = c.execute(
                text(
                    "INSERT INTO module_versions (module_id, version_no, author_id) "
                    "SELECT :m, coalesce(max(version_no), 0) + 1, :a FROM module_versions WHERE module_id = :m "
                    "RETURNING id"
                ),
                {"m": mod, "a": author},
            ).scalar()
            c.execute(
                text(
                    "INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position) "
                    "VALUES (:mv, :m, :l, 0)"
                ),
                {"mv": mv, "m": mod, "l": lessons[0]},
            )
            for loc, vid in (("fr", fr), ("en", versions["en"])):
                c.execute(
                    text(
                        "INSERT INTO module_version_lesson_versions (module_version_id, lesson_id, locale, "
                        "lesson_version_id) VALUES (:mv, :l, :loc, :v)"
                    ),
                    {"mv": mv, "l": lessons[0], "loc": loc, "v": vid},
                )
            act(author, "UPDATE module_versions SET status = 'medical_review' WHERE id = :x", {"x": mv})
            act(reviewer, "UPDATE module_versions SET status = 'approved' WHERE id = :x", {"x": mv})
            rid = c.execute(
                text(
                    "INSERT INTO program_releases (program_id, release_no, author_id) VALUES (:p, 1, :a) RETURNING id"
                ),
                {"p": program, "a": author},
            ).scalar()
            c.execute(
                text(
                    "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position) "
                    "VALUES (:r, :m, :mv, 0)"
                ),
                {"r": rid, "m": mod, "mv": mv},
            )
            act(author, "UPDATE program_releases SET status = 'medical_review' WHERE id = :r", {"r": rid})
            act(reviewer, "UPDATE program_releases SET status = 'approved' WHERE id = :r", {"r": rid})
            act(reviewer, "UPDATE program_releases SET status = 'published' WHERE id = :r", {"r": rid})
            releases.append(rid)
        c.execute(
            text(
                "INSERT INTO lesson_versions (lesson_id, locale, version_no, title, body) "
                "VALUES (:l, 'fr', 1, 'Brouillon (test)', CAST('{\"blocks\":[]}' AS jsonb))"
            ),
            {"l": lessons[1]},
        )
    yield ids
    # L'historique éditorial est immuable : les éditions sont archivées (le programme et le module
    # sortent du catalogue) au lieu d'être supprimées. Les codes de test sont uniques par exécution.
    with sync_engine.begin() as c:
        c.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": str(reviewer)})
        for rid in releases:
            c.execute(text("UPDATE program_releases SET status = 'archived' WHERE id = :r"), {"r": rid})

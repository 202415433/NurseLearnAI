"""Outils de test du workflow éditorial (migration 0005), exécutés sur PostgreSQL réel.

Chaque transition est jouée comme le fera l'API : dans une transaction qui déclare l'acteur
(`nl.actor_id`) et, si besoin, le motif (`nl.decision_comment`).
"""

from __future__ import annotations

import json
import uuid

from conftest import Db


def new_id() -> str:
    return str(uuid.uuid4())


def q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Editorial:
    def __init__(self, db: Db):
        self.db = db

    # ------------------------------------------------------------------ transactions avec acteur
    def _tx(self, actor: str | None, sql: str, comment: str | None) -> str:
        parts = ["BEGIN;"]
        if actor:
            parts.append(f"SELECT set_config('nl.actor_id', '{actor}', true);")
        if comment:
            parts.append(f"SELECT set_config('nl.decision_comment', {q(comment)}, true);")
        parts += [sql.rstrip(";") + ";", "COMMIT;"]
        return " ".join(parts)

    def run(self, actor: str | None, sql: str, comment: str | None = None) -> str:
        return self.db.sql(self._tx(actor, sql, comment))

    def fails(self, actor: str | None, sql: str, comment: str | None = None) -> str:
        return self.db.fails(self._tx(actor, sql, comment))

    # ------------------------------------------------------------------ contenus
    def lesson_version(
        self, lesson: str, author: str | None, locale: str = "fr", blocks: int = 2, jurisdiction: str | None = None
    ) -> str:
        vid = new_id()
        body = json.dumps(
            {"blocks": [{"id": f"b{i}", "type": "paragraph", "text": f"Texte {i}"} for i in range(blocks)]}
        )
        jur = f"'{jurisdiction}'" if jurisdiction else "NULL"
        author_sql = f"'{author}'" if author else "NULL"
        self.db.sql(
            "INSERT INTO lesson_versions (id, lesson_id, locale, jurisdiction_id, version_no, title, body, author_id) "
            f"SELECT '{vid}', '{lesson}', '{locale}', {jur}, coalesce(max(version_no), 0) + 1, 'Leçon de test', "
            f"{q(body)}, {author_sql} FROM lesson_versions WHERE lesson_id = '{lesson}' AND locale = '{locale}' "
            f"AND jurisdiction_id IS NOT DISTINCT FROM {jur}"
        )
        return vid

    def cite_source(self, version: str, status: str = "active") -> str:
        rid = new_id()
        superseded = ""
        if status == "superseded":
            other = self.cite_source_reference_only()
            superseded = f", superseded_by = '{other}'"
        self.db.sql(
            f"INSERT INTO medical_references (id, kind, title, url) VALUES ('{rid}', 'guideline', 'Référence fictive', "
            f"'https://example.test/{rid}')"
        )
        if status != "active":
            self.db.sql(f"UPDATE medical_references SET status = '{status}'{superseded} WHERE id = '{rid}'")
        self.db.sql(
            f"INSERT INTO lesson_version_references (lesson_version_id, reference_id) VALUES ('{version}', '{rid}')"
        )
        return rid

    def cite_source_reference_only(self) -> str:
        rid = new_id()
        self.db.sql(
            f"INSERT INTO medical_references (id, kind, title, url) VALUES ('{rid}', 'guideline', 'Remplaçante', "
            f"'https://example.test/{rid}')"
        )
        return rid

    def set_status(self, table: str, row: str, status: str, actor: str | None, extra: str = "", comment=None) -> str:
        return self.run(actor, f"UPDATE {table} SET status = '{status}'{extra} WHERE id = '{row}'", comment)

    def status(self, table: str, row: str) -> str:
        return self.db.sql(f"SELECT status FROM {table} WHERE id = '{row}'")

    # ------------------------------------------------------------------ parcours d'une version
    def submit(self, version: str, actor: str) -> None:
        self.set_status("lesson_versions", version, "medical_review", actor)

    def approve(self, version: str, reviewer: str) -> None:
        self.set_status("lesson_versions", version, "approved", reviewer, ", next_review_due = current_date + 365")

    def approved_version(self, lesson: str, author: str, reviewer: str, locale: str = "fr", sources: int = 1) -> str:
        vid = self.lesson_version(lesson, author, locale)
        for _ in range(sources):
            self.cite_source(vid)
        self.submit(vid, author)
        self.approve(vid, reviewer)
        return vid

    # ------------------------------------------------------------------ éditions
    @staticmethod
    def manifest(*modules: tuple[str, list[tuple[str, dict[str, str]]]], required: bool = True) -> str:
        data = {
            "modules": [
                {
                    "module_id": module,
                    "position": mpos,
                    "required": required,
                    "lessons": [
                        {"lesson_id": lesson, "position": lpos, "versions": versions}
                        for lpos, (lesson, versions) in enumerate(lessons)
                    ],
                }
                for mpos, (module, lessons) in enumerate(modules)
            ]
        }
        return json.dumps(data)

    def release(self, program: str, author: str, manifest: str) -> str:
        rid = new_id()
        self.db.sql(
            "INSERT INTO program_releases (id, program_id, release_no, manifest, author_id) "
            f"SELECT '{rid}', '{program}', coalesce(max(release_no), 0) + 1, {q(manifest)}, '{author}' "
            f"FROM program_releases WHERE program_id = '{program}'"
        )
        return rid

    def publish_release(self, release: str, author: str, reviewer: str) -> None:
        self.set_status("program_releases", release, "medical_review", author)
        self.set_status("program_releases", release, "approved", reviewer)
        self.set_status("program_releases", release, "published", reviewer)


class EditorialV2(Editorial):
    """Workflow à jour (0006, ADR-0010) : versions de modules et éditions relationnelles."""

    # ------------------------------------------------------------------ sources vérifiées
    def verified_source(self, version: str, verifier: str) -> str:
        rid = self.cite_source(version)
        self.run(verifier, f"UPDATE medical_references SET last_verified_at = current_date WHERE id = '{rid}'")
        return rid

    def approved_version(  # type: ignore[override]
        self, lesson: str, author: str, reviewer: str, locale: str = "fr", sources: int = 1, verified: bool = True
    ) -> str:
        vid = self.lesson_version(lesson, author, locale)
        for _ in range(sources):
            if verified:
                self.verified_source(vid, reviewer)
            else:
                self.cite_source(vid)
        self.submit(vid, author)
        self.approve(vid, reviewer)
        return vid

    # ------------------------------------------------------------------ versions de modules
    def module_version(self, module: str, author: str, lessons: list[tuple[str, dict[str, str]]]) -> str:
        mvid = new_id()
        self.db.sql(
            "INSERT INTO module_versions (id, module_id, version_no, author_id) "
            f"SELECT '{mvid}', '{module}', coalesce(max(version_no), 0) + 1, '{author}' "
            f"FROM module_versions WHERE module_id = '{module}'"
        )
        for pos, (lesson, versions) in enumerate(lessons):
            self.db.sql(
                "INSERT INTO module_version_lessons (module_version_id, module_id, lesson_id, position) "
                f"VALUES ('{mvid}', '{module}', '{lesson}', {pos})"
            )
            for locale, lv in versions.items():
                self.db.sql(
                    "INSERT INTO module_version_lesson_versions (module_version_id, lesson_id, locale, lesson_version_id) "
                    f"VALUES ('{mvid}', '{lesson}', '{locale}', '{lv}')"
                )
        return mvid

    def approved_module_version(
        self, module: str, author: str, reviewer: str, lessons: list[tuple[str, dict[str, str]]]
    ) -> str:
        mvid = self.module_version(module, author, lessons)
        self.set_status("module_versions", mvid, "medical_review", author)
        self.set_status("module_versions", mvid, "approved", reviewer)
        return mvid

    # ------------------------------------------------------------------ éditions relationnelles
    def release_v2(
        self, program: str, author: str, modules: list[tuple[str, str]], prerequisites: list[tuple[str, str]] = ()
    ) -> str:
        rid = new_id()
        self.db.sql(
            "INSERT INTO program_releases (id, program_id, release_no, author_id) "
            f"SELECT '{rid}', '{program}', coalesce(max(release_no), 0) + 1, '{author}' "
            f"FROM program_releases WHERE program_id = '{program}'"
        )
        for pos, (module, mv) in enumerate(modules):
            self.db.sql(
                "INSERT INTO program_release_modules (release_id, module_id, module_version_id, position) "
                f"VALUES ('{rid}', '{module}', '{mv}', {pos})"
            )
        for module, prereq in prerequisites:
            self.db.sql(
                "INSERT INTO program_release_prerequisites (release_id, module_id, prerequisite_module_id) "
                f"VALUES ('{rid}', '{module}', '{prereq}')"
            )
        return rid

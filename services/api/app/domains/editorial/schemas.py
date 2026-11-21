"""Contrats de l'API éditoriale : stricts (`extra="forbid"`), bornés, sans champ d'état modifiable.

Aucun schéma d'entrée n'accepte `status`, `author_id`, `reviewed_by`, `published_by`, `verified_by`
ni d'identifiant d'acteur : ces valeurs viennent de la session et de la base.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.editorial.blocks import Body, check_text

Code = Annotated[str, Field(pattern=r"^[a-z0-9_-]{2,80}$")]
Level = Literal["beginner", "fundamental", "intermediate", "advanced", "professional_prep"]
AccessTier = Literal["free", "premium", "organization"]
Locale = Literal["fr", "en"]
Kind = Literal["lesson-versions", "module-versions", "releases"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class I18nText(Strict):
    fr: Annotated[str, Field(min_length=2, max_length=300)]
    en: Annotated[str | None, Field(min_length=2, max_length=300)] = None

    @field_validator("fr", "en")
    @classmethod
    def _plain(cls, v: str | None) -> str | None:
        return check_text(v) if v else v

    def as_json(self) -> dict[str, str]:
        return {k: v for k, v in self.model_dump().items() if v}


# ------------------------------------------------------------------ structure de travail
class ProgramIn(Strict):
    code: Code
    profession_id: uuid.UUID
    specialty_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    title: I18nText
    summary: I18nText | None = None
    level: Level
    access_tier: AccessTier = "free"
    is_medical: bool = True

    @model_validator(mode="after")
    def _tier(self) -> ProgramIn:
        if (self.access_tier == "organization") != (self.organization_id is not None):
            raise ValueError("access_tier « organization » ⇔ programme d'établissement")
        return self


class ProgramPatch(Strict):
    title: I18nText | None = None
    summary: I18nText | None = None
    level: Level | None = None
    specialty_id: uuid.UUID | None = None
    is_medical: bool | None = None


class ModuleIn(Strict):
    code: Code
    specialty_id: uuid.UUID
    organization_id: uuid.UUID | None = None
    title: I18nText
    summary: I18nText | None = None
    level: Level
    estimated_minutes: Annotated[int, Field(ge=1, le=6000)] = 30
    access_tier: AccessTier = "free"

    @model_validator(mode="after")
    def _tier(self) -> ModuleIn:
        if self.access_tier == "organization" and self.organization_id is None:
            raise ValueError("access_tier « organization » exige un établissement")
        return self


class ModulePatch(Strict):
    title: I18nText | None = None
    summary: I18nText | None = None
    level: Level | None = None
    estimated_minutes: Annotated[int | None, Field(ge=1, le=6000)] = None


LessonKind = Literal["theory", "video", "revision_sheet", "interactive", "case_study", "anatomy"]


class LessonIn(Strict):
    code: Code
    position: Annotated[int, Field(ge=0, le=1000)]
    kind: LessonKind = "theory"
    estimated_minutes: Annotated[int, Field(ge=1, le=600)] = 10


class LessonPatch(Strict):
    position: Annotated[int | None, Field(ge=0, le=1000)] = None
    kind: LessonKind | None = None
    estimated_minutes: Annotated[int | None, Field(ge=1, le=600)] = None


# ------------------------------------------------------------------ versions de leçon
class LessonVersionCreate(Strict):
    from_version_id: uuid.UUID | None = None
    locale: Locale | None = None
    jurisdiction_id: uuid.UUID | None = None
    title: Annotated[str | None, Field(min_length=2, max_length=300)] = None
    body: Body | None = None
    change_summary: Annotated[str | None, Field(max_length=4000)] = None

    @model_validator(mode="after")
    def _source(self) -> LessonVersionCreate:
        if self.from_version_id is None and (self.locale is None or self.title is None):
            raise ValueError("locale et title sont obligatoires sans from_version_id")
        if self.from_version_id is not None and (self.locale or self.jurisdiction_id):
            raise ValueError("la copie reprend la langue et la juridiction de la version d'origine")
        return self


class LessonVersionPatch(Strict):
    title: Annotated[str | None, Field(min_length=2, max_length=300)] = None
    body: Body | None = None
    change_summary: Annotated[str | None, Field(max_length=4000)] = None


class SourceRef(Strict):
    reference_id: uuid.UUID
    locator: Annotated[str | None, Field(max_length=200)] = None


class SourcesPut(Strict):
    references: Annotated[list[SourceRef], Field(max_length=30)]

    @model_validator(mode="after")
    def _unique(self) -> SourcesPut:
        ids = [r.reference_id for r in self.references]
        if len(ids) != len(set(ids)):
            raise ValueError("source citée deux fois")
        return self


# ------------------------------------------------------------------ versions de modules
class ModuleVersionLesson(Strict):
    lesson_id: uuid.UUID
    position: Annotated[int, Field(ge=0, le=1000)]
    required: bool = True
    versions: dict[Locale, uuid.UUID]

    @model_validator(mode="after")
    def _fr(self) -> ModuleVersionLesson:
        if "fr" not in self.versions:
            raise ValueError("version française obligatoire")
        return self


class ModuleVersionIn(Strict):
    from_version_id: uuid.UUID | None = None
    lessons: Annotated[list[ModuleVersionLesson] | None, Field(max_length=100)] = None
    change_summary: Annotated[str | None, Field(max_length=4000)] = None

    @model_validator(mode="after")
    def _shape(self) -> ModuleVersionIn:
        if (self.from_version_id is None) == (self.lessons is None):
            raise ValueError("fournir soit lessons, soit from_version_id")
        _unique_positions(self.lessons or [], "lesson_id")
        return self


class ModuleVersionPatch(Strict):
    lessons: Annotated[list[ModuleVersionLesson], Field(min_length=1, max_length=100)]
    change_summary: Annotated[str | None, Field(max_length=4000)] = None

    @model_validator(mode="after")
    def _shape(self) -> ModuleVersionPatch:
        _unique_positions(self.lessons, "lesson_id")
        return self


# ------------------------------------------------------------------ éditions
class ReleaseModule(Strict):
    module_version_id: uuid.UUID
    position: Annotated[int, Field(ge=0, le=1000)]
    required: bool = True


class Prerequisite(Strict):
    module_id: uuid.UUID
    requires: uuid.UUID

    @model_validator(mode="after")
    def _self(self) -> Prerequisite:
        if self.module_id == self.requires:
            raise ValueError("un module ne peut pas être son propre prérequis")
        return self


class ReleaseIn(Strict):
    modules: Annotated[list[ReleaseModule], Field(min_length=1, max_length=50)]
    prerequisites: Annotated[list[Prerequisite], Field(max_length=200)] = []
    change_summary: Annotated[str, Field(min_length=3, max_length=4000)]

    @model_validator(mode="after")
    def _shape(self) -> ReleaseIn:
        _unique_positions(self.modules, "module_version_id")
        pairs = [(p.module_id, p.requires) for p in self.prerequisites]
        if len(pairs) != len(set(pairs)):
            raise ValueError("prérequis en double")
        return self


def _unique_positions(items: list[Any], key: str) -> None:
    positions = [i.position for i in items]
    keys = [getattr(i, key) for i in items]
    if len(positions) != len(set(positions)) or len(keys) != len(set(keys)):
        raise ValueError(f"positions ou {key} en double")


# ------------------------------------------------------------------ transitions
class RequestChangesIn(Strict):
    comment: Annotated[str, Field(min_length=5, max_length=4000)]


class ApproveIn(Strict):
    next_review_due: date | None = None


class ReasonIn(Strict):
    reason: Annotated[str, Field(min_length=5, max_length=1000)]


# ------------------------------------------------------------------ sources
class ReferenceIn(Strict):
    kind: Literal["guideline", "regulation", "textbook", "article", "official_website", "standard"]
    title: Annotated[str, Field(min_length=3, max_length=500)]
    authors: Annotated[str | None, Field(max_length=500)] = None
    publisher: Annotated[str | None, Field(max_length=200)] = None
    publication_year: Annotated[int | None, Field(ge=1900, le=2100)] = None
    url: Annotated[str | None, Field(pattern=r"^https://[^\s<>\"]{3,2000}$")] = None
    doi: Annotated[str | None, Field(pattern=r"^10\.\d{4,9}/\S{1,200}$")] = None
    isbn: Annotated[str | None, Field(pattern=r"^[0-9X-]{10,17}$")] = None
    jurisdiction_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _locator(self) -> ReferenceIn:
        if not (self.url or self.doi or self.isbn):
            raise ValueError("url, doi ou isbn obligatoire")
        return self


# ------------------------------------------------------------------ sorties
class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DecisionOut(Out):
    decision: str
    from_status: str
    to_status: str
    actor_id: uuid.UUID
    organization_id: uuid.UUID | None
    content_sha256: str | None
    comment: str | None
    decided_at: datetime


class ReviewQueueItem(Out):
    kind: Kind
    id: uuid.UUID
    title: str
    organization_id: uuid.UUID | None
    author_id: uuid.UUID | None
    submitted_at: datetime | None
    can_review: bool

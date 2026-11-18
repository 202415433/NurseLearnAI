"""Modèles SQLAlchemy du périmètre du Phase 1.2 (référentiels et catalogue).

Le schéma fait foi (database/schema, ADR-0002) : ces modèles en sont une
projection en lecture. Le test `test_models_match_schema` vérifie que chaque
colonne déclarée ici existe en base avec un type et une nullabilité compatibles.
Les autres tables seront modélisées en Phase 2.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True)


class Locale(Base):
    __tablename__ = "locales"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean)


class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name_i18n: Mapped[dict] = mapped_column(JSONB)


class Jurisdiction(Base):
    __tablename__ = "jurisdictions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    country_code: Mapped[str | None] = mapped_column(String(2), ForeignKey("countries.code"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jurisdictions.id"))
    kind: Mapped[str] = mapped_column(Text)
    name_i18n: Mapped[dict] = mapped_column(JSONB)
    default_locale: Mapped[str] = mapped_column(Text, ForeignKey("locales.code"))
    is_active: Mapped[bool] = mapped_column(Boolean)


class Profession(Base):
    __tablename__ = "professions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jurisdictions.id"))
    code: Mapped[str] = mapped_column(Text)
    title_i18n: Mapped[dict] = mapped_column(JSONB)
    category: Mapped[str] = mapped_column(Text)
    is_regulated: Mapped[bool] = mapped_column(Boolean)
    regulator_name: Mapped[str | None] = mapped_column(Text)
    regulator_url: Mapped[str | None] = mapped_column(Text)
    official_path_i18n: Mapped[dict | None] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(Text)


class Specialty(Base):
    __tablename__ = "specialties"
    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    catalog_group: Mapped[str] = mapped_column(String(1))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("specialties.id"))
    name_i18n: Mapped[dict] = mapped_column(JSONB)
    sort_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)


class Program(Base):
    __tablename__ = "programs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    profession_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("professions.id"))
    specialty_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("specialties.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title_i18n: Mapped[dict] = mapped_column(JSONB)
    summary_i18n: Mapped[dict | None] = mapped_column(JSONB)
    level: Mapped[str] = mapped_column(Text)
    access_tier: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)


class Module(Base):
    __tablename__ = "modules"
    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    specialty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("specialties.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    level: Mapped[str] = mapped_column(Text)
    title_i18n: Mapped[dict] = mapped_column(JSONB)
    summary_i18n: Mapped[dict | None] = mapped_column(JSONB)
    estimated_minutes: Mapped[int] = mapped_column(Integer)
    access_tier: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)


class ProgramModule(Base):
    __tablename__ = "program_modules"
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("programs.id"), primary_key=True)
    module_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modules.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    is_required: Mapped[bool] = mapped_column(Boolean)


class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[uuid.UUID] = _uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modules.id"))
    code: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    estimated_minutes: Mapped[int] = mapped_column(Integer)


class LessonVersion(Base):
    __tablename__ = "lesson_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    lesson_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lessons.id"))
    locale: Mapped[str] = mapped_column(Text)
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jurisdictions.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_due: Mapped[date | None] = mapped_column(Date)

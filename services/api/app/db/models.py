"""Modèles SQLAlchemy du périmètre du Phase 1.2 (référentiels et catalogue).

Le schéma fait foi (database/schema, ADR-0002) : ces modèles en sont une
projection en lecture. Le test `test_models_match_schema` vérifie que chaque
colonne déclarée ici existe en base avec un type et une nullabilité compatibles.
Phase 2.1 : tables d'identité et d'accès (ADR-0007), y compris celles de la migration 0004.
Phase 2.2.1 : éditions, inscriptions, historique et décisions éditoriales (migration 0005, ADR-0009).
Les autres tables seront modélisées par les lots qui les utilisent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
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
    is_medical: Mapped[bool] = mapped_column(Boolean)


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
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    content_sha256: Mapped[str | None] = mapped_column(String(64))


# --------------------------------------------------------------------------------------------
# Identité et accès (Phase 2.1)
# --------------------------------------------------------------------------------------------
class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jurisdictions.id"))
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT)
    password_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean)
    locale: Mapped[str] = mapped_column(Text, ForeignKey("locales.code"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Migration 0004
    adult_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(Text)
    privacy_policy_version: Mapped[str | None] = mapped_column(Text)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Profile(Base):
    __tablename__ = "profiles"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jurisdictions.id"))
    target_profession_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("professions.id"))
    timezone: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text)
    name_i18n: Mapped[dict] = mapped_column(JSONB)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("roles.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(Text, primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("roles.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Empreinte HMAC de l'identifiant de session opaque (nom de colonne historique conservé).
    refresh_token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # durée absolue
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("auth_sessions.id"))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class OneTimeToken(Base):
    __tablename__ = "one_time_tokens"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT)
    role_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("roles.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_role: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB)


# --------------------------------------------------------------------------------------------
# Moteur pédagogique (Phase 2.2.1, migration 0005) — les règles sont portées par la base :
# états et transitions, auteur ≠ relecteur, immuabilité, idempotence, isolation.
# --------------------------------------------------------------------------------------------
class ProgramRelease(Base):
    __tablename__ = "program_releases"
    id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("programs.id"))
    release_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict] = mapped_column(JSONB)
    change_summary: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Enrollment(Base):
    __tablename__ = "enrollments"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("programs.id"))
    release_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("program_releases.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_lesson_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("lessons.id"))
    last_block_id: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningEvent(Base):
    __tablename__ = "learning_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    release_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("program_releases.id"))
    client_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(Text)
    module_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("modules.id"))
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("lessons.id"))
    lesson_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("lesson_versions.id"))
    block_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)


class EditorialDecision(Base):
    __tablename__ = "editorial_decisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    from_status: Mapped[str] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

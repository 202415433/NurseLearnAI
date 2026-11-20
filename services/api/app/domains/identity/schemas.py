"""Schémas d'entrée et de sortie de l'identité (validation stricte, champs inconnus refusés)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise ValueError("invalid_email")
    return value


Email = Annotated[str, Field(min_length=3, max_length=254), AfterValidator(_email)]
Password = Annotated[str, Field(min_length=1, max_length=1024)]
Token = Annotated[str, StringConstraints(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LocaleCode = Literal["fr", "en"]
InvitableRole = Literal["instructor", "medical_reviewer", "org_admin"]


class Strict(BaseModel):
    # Tout champ inattendu (ex. « role ») est refusé : impossible de s'attribuer un rôle à l'inscription.
    model_config = ConfigDict(extra="forbid")


class RegisterIn(Strict):
    email: Email
    password: Password
    display_name: DisplayName
    locale: LocaleCode = "fr"
    adult_attestation: bool
    accept_terms: bool
    accept_privacy: bool
    # Champ piège invisible pour les humains : rempli = robot (réponse identique, rien n'est créé).
    website: Annotated[str, Field(max_length=200)] = ""


class EmailIn(Strict):
    email: Email


class TokenIn(Strict):
    token: Token


class LoginIn(Strict):
    email: Email
    password: Password


class PasswordIn(Strict):
    password: Password


class ResetIn(Strict):
    token: Token
    new_password: Password


class ChangePasswordIn(Strict):
    current_password: Password
    new_password: Password


class ProfileIn(Strict):
    display_name: DisplayName | None = None
    locale: LocaleCode | None = None


class InvitationIn(Strict):
    email: Email
    role: InvitableRole
    organization_id: uuid.UUID | None = None
    locale: LocaleCode = "fr"


class Accepted(BaseModel):
    status: str


class SessionOut(BaseModel):
    session_token: str
    expires_at: datetime


class RoleOut(BaseModel):
    role: str
    organization_id: uuid.UUID | None
    organization_name: str | None


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    locale: str
    status: str
    roles: list[RoleOut]
    permissions: list[str]
    sensitive_fresh: bool
    session_expires_at: datetime


class LoginOut(SessionOut):
    user: MeOut


class RefreshOut(BaseModel):
    rotated: bool
    session_token: str | None = None
    expires_at: datetime
    sensitive_fresh: bool


class DeviceSessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    ip_hint: str | None
    current: bool


class OrganizationOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    roles: list[str]


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: str
    roles: list[str]


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    organization_id: uuid.UUID | None
    status: Literal["pending", "accepted", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime


class InvitationCreated(BaseModel):
    id: uuid.UUID
    expires_at: datetime


class InvitationPreview(BaseModel):
    role: str
    organization_name: str | None
    expires_at: datetime


class PrivateProgramOut(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    status: str

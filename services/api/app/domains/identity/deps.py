"""Dépendances FastAPI de l'identité : base en écriture, session courante, permissions."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemError
from app.domains.identity.sessions import AuthContext, authenticate

PROBLEM_BASE = "https://nurselearn.ai/problems/"


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Session de base de données ; les écritures sont validées explicitement (commit)."""
    async with request.app.state.sessionmaker() as session:
        yield session


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def client_ip(request: Request) -> str | None:
    candidate = getattr(request.state, "client_ip", None) or (request.client.host if request.client else None)
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None  # ex. « testclient »


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def unauthorized() -> ProblemError:
    return ProblemError(401, "Authentification requise", type_=PROBLEM_BASE + "unauthenticated")


def forbidden(detail: str = "Action non autorisée") -> ProblemError:
    return ProblemError(403, detail, type_=PROBLEM_BASE + "forbidden")


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


async def optional_auth(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext | None:
    # Seul l'en-tête Authorization est accepté : l'API ignore les cookies (aucune attaque CSRF possible
    # contre elle depuis un navigateur ; le serveur Web garde le cookie HttpOnly et relaie le jeton).
    token = _bearer(authorization)
    if token is None:
        return None
    ctx = await authenticate(
        db, request.app.state.settings, token, ip=client_ip(request), user_agent=user_agent(request)
    )
    if ctx is not None:
        request.state.auth = ctx
    return ctx


async def require_auth(ctx: Annotated[AuthContext | None, Depends(optional_auth)]) -> AuthContext:
    if ctx is None:
        raise unauthorized()
    return ctx


async def require_sensitive(ctx: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
    """Opérations sensibles : refusées après 30 min d'inactivité tant qu'on ne s'est pas ré-authentifié."""
    if not ctx.sensitive_fresh:
        raise ProblemError(
            403,
            "Confirmez votre mot de passe pour poursuivre",
            type_=PROBLEM_BASE + "reauthentication-required",
        )
    return ctx


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ProblemError(404, "Ressource introuvable", type_=PROBLEM_BASE + "not-found") from exc


Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[AuthContext, Depends(require_auth)]
SensitiveAuth = Annotated[AuthContext, Depends(require_sensitive)]

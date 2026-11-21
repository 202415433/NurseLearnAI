"""Transaction éditoriale : acteur déclaré, traduction des erreurs, audit, ETag.

Chaque écriture s'exécute dans UNE transaction qui commence par
`set_config('nl.actor_id', <utilisateur de la session>, true)` — jamais une valeur du client —,
puis, si besoin, `nl.decision_comment`. Les déclencheurs PostgreSQL des migrations 0005 et 0006
revérifient alors habilitation, portée, indépendance et immuabilité.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import ProblemError
from app.domains.editorial.errors import BASE, translate
from app.domains.identity.deps import client_ip, user_agent
from app.domains.identity.sessions import AuthContext

CONTENT_KEYS = frozenset({"body", "blocks", "title", "text", "comment", "reason", "content", "summary"})


@asynccontextmanager
async def editorial_tx(db: AsyncSession, ctx: AuthContext, comment: str | None = None) -> AsyncIterator[None]:
    try:
        await db.execute(text("SELECT set_config('nl.actor_id', :a, true)"), {"a": str(ctx.user_id)})
        await db.execute(text("SELECT set_config('nl.decision_comment', :c, true)"), {"c": comment or ""})
        yield
        await db.commit()
    except DBAPIError as exc:
        await db.rollback()
        raise translate(exc) from None
    except BaseException:
        await db.rollback()
        raise


async def record(
    db: AsyncSession,
    request: Request,
    ctx: AuthContext,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str,
    org: uuid.UUID | None,
    **metadata: Any,
) -> None:
    """Audit sans contenu : type, identifiant, portée, état, empreinte (jamais le corps ni un secret)."""
    leaked = CONTENT_KEYS.intersection(metadata)
    if leaked:  # garde-fou : aucun contenu pédagogique ni texte libre dans le journal d'audit
        raise ValueError(f"Métadonnées d'audit interdites : {sorted(leaked)}")
    await audit(
        db,
        action,
        entity_type,
        entity_id,
        actor=ctx.user_id,
        actor_role=ctx.primary_role,
        organization_id=org,
        ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={k: v for k, v in metadata.items() if v is not None},
    )


def etag_of(payload: Any) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    return f'"{digest[:32]}"'


def check_if_match(request: Request, current: str) -> None:
    header = request.headers.get("if-match")
    if not header:
        raise ProblemError(
            428,
            "En-tête If-Match obligatoire (valeur ETag de la dernière lecture)",
            type_=BASE + "precondition-required",
        )
    candidates = [h.strip() for h in header.split(",")]
    if current not in candidates and current.strip('"') not in [c.strip('"') for c in candidates]:
        raise ProblemError(
            412,
            "Le brouillon a été modifié depuis votre lecture ; rechargez-le",
            type_=BASE + "stale-draft",
            extra={"etag": current},
        )

"""Journal d'audit (table audit_logs, en ajout seul et chaînée par empreintes en base).

Règle : aucune donnée secrète (mot de passe, jeton, identifiant de session) ni courriel en clair
dans les métadonnées ; utiliser `pseudonymize()` pour corréler une adresse tentée.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_FORBIDDEN_KEYS = {"password", "token", "session_token", "secret", "hash", "password_hash", "email"}


async def audit(
    db: AsyncSession,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    *,
    actor: uuid.UUID | None = None,
    actor_role: str | None = None,
    organization_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    meta = metadata or {}
    leaked = _FORBIDDEN_KEYS.intersection(k.lower() for k in meta)
    if leaked:  # garde-fou de développement : jamais de secret dans le journal
        raise ValueError(f"Métadonnées d'audit interdites : {sorted(leaked)}")
    await db.execute(
        text(
            "INSERT INTO audit_logs (actor_user_id, actor_role, action, entity_type, entity_id, organization_id, "
            "ip, user_agent, metadata) VALUES (:actor, :role, :action, :etype, :eid, :org, "
            "CAST(CAST(:ip AS text) AS inet), :ua, CAST(CAST(:meta AS text) AS jsonb))"
        ),
        {
            "actor": actor,
            "role": actor_role,
            "action": action,
            "etype": entity_type,
            "eid": str(entity_id) if entity_id is not None else None,
            "org": organization_id,
            "ip": ip,
            "ua": (user_agent or "")[:200] or None,
            "meta": json.dumps(meta, ensure_ascii=False, sort_keys=True),
        },
    )

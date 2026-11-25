"""Quotas de stockage des médias, en lecture seule (2.2.3-e) — `GET /api/v1/editorial/quotas/media`.

Affichage seulement : la réservation reste atomique en base (`nl_media_reserve`, 0007). L'occupation
est DÉRIVÉE de `media_assets` (même calcul que `nl_media_scope_usage`) ; le quota vient de
`media_usage` ou, faute de ligne, du quota par défaut (1, 5, 20 Gio). Aucune écriture.

Portées montrées : l'utilisateur ; chaque établissement où il peut créer des contenus ; la plateforme
(catalogue public) seulement s'il peut y créer. Aucun chiffre d'un autre établissement n'est exposé.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.domains.identity.deps import Auth, Db

router = APIRouter(prefix="/api/v1/editorial/quotas", tags=["médias protégés"])

GIB = 1073741824
DEFAULT_QUOTA = {"user": 1 * GIB, "organization": 5 * GIB, "platform": 20 * GIB}
# Limites par fichier (D15), répétées pour l'affichage (source : MAX_BYTES de routes.py).
MIB = 1048576
FILE_LIMITS = {"image": 5 * MIB, "document": 20 * MIB, "video": 200 * MIB, "captions": 1 * MIB}

USAGE = (
    "SELECT coalesce(sum(byte_size) FILTER (WHERE state <> 'pending_upload' AND purged_at IS NULL), 0)::bigint "
    "AS used, "
    "coalesce(sum(declared_bytes) FILTER (WHERE state = 'pending_upload' AND deleted_at IS NULL "
    "AND reservation_expires_at > now()), 0)::bigint AS reserved FROM media_assets WHERE "
)
WHERE = {
    "user": "uploaded_by = :user",
    "organization": "organization_id = :org",
    "platform": "organization_id IS NULL",
}
QUOTA = {
    "user": "SELECT quota_bytes FROM media_usage WHERE scope_kind = 'user' AND user_id = :user",
    "organization": "SELECT quota_bytes FROM media_usage WHERE scope_kind = 'organization' AND organization_id = :org",
    "platform": "SELECT quota_bytes FROM media_usage WHERE scope_kind = 'platform'",
}


async def _scope(db: Any, scope: str, user: uuid.UUID, org: uuid.UUID | None) -> dict[str, Any]:
    params = {"user": user, "org": org}
    row = (await db.execute(text(USAGE + WHERE[scope]), params)).mappings().one()
    quota = (await db.execute(text(QUOTA[scope]), params)).scalar()
    return {
        "scope": scope,
        "organization_id": org,
        "used_bytes": int(row["used"]),
        "reserved_bytes": int(row["reserved"]),
        "quota_bytes": int(quota) if quota is not None else DEFAULT_QUOTA[scope],
    }


@router.get("/media", summary="Occupation et quotas de stockage des médias (lecture seule)")
async def media_quotas(ctx: Auth, db: Db) -> dict[str, Any]:
    scopes = [await _scope(db, "user", ctx.user_id, None)]
    orgs = sorted({org for _, org in ctx.roles if org is not None and ctx.can("content:create", org)}, key=str)
    for org in orgs:
        scopes.append(await _scope(db, "organization", ctx.user_id, org))
    if ctx.can("content:create", None):
        scopes.append(await _scope(db, "platform", ctx.user_id, None))
    return {"scopes": scopes, "file_limits": FILE_LIMITS}

"""Limitation de débit des routes /api/ (fenêtre fixe d'une minute, stockée dans Redis).

Protection contre l'aspiration et l'abus tant que l'authentification n'existe pas
(Phase 2). L'adresse IP n'est jamais stockée en clair : seule une empreinte
tronquée sert de clé, avec expiration automatique. Si Redis est indisponible,
la requête passe (fail-open) et l'incident est journalisé.
"""
from __future__ import annotations

import hashlib
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import problem_response

log = logging.getLogger("nurselearn.ratelimit")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int, salt: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.per_minute = per_minute
        self.salt = salt

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        redis = getattr(request.app.state, "redis", None)
        if self.per_minute <= 0 or redis is None or not request.url.path.startswith("/api/"):
            return await call_next(request)
        client = request.client.host if request.client else "inconnu"
        fingerprint = hashlib.sha256(f"{self.salt}:{client}".encode()).hexdigest()[:16]
        window = int(time.time() // 60)
        key = f"nl:rl:{fingerprint}:{window}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 70)
        except Exception:  # noqa: BLE001 — fail-open
            log.warning("ratelimit_indisponible")
            return await call_next(request)
        if count > self.per_minute:
            retry = 60 - int(time.time()) % 60
            response = problem_response(request, 429, "Too Many Requests", "Trop de requêtes, réessayez plus tard",
                                        "https://nurselearn.ai/problems/rate-limit")
            response.headers["Retry-After"] = str(retry)
            return response
        return await call_next(request)

"""Limitation de débit des routes /api/ (fenêtre fixe d'une minute, compteurs partagés dans Redis).

Protection contre l'aspiration et l'abus tant que l'authentification n'existe pas.

- **Plusieurs instances** : les compteurs vivent dans Redis et la clé ne dépend que du client,
  de la minute et d'un sel commun à toutes les instances (`RATE_LIMIT_SALT`) : chaque instance
  incrémente le même compteur.
- **Mandataires** : derrière un reverse proxy ou le serveur Web, le client réel est lu dans
  `X-Forwarded-For`, uniquement si le pair est déclaré dans `TRUSTED_PROXIES` (voir clientip.py).
- **Vie privée** : l'adresse n'est jamais stockée en clair (empreinte tronquée, expiration 70 s).
- **Disponibilité** : si Redis est indisponible, la requête passe (fail-open) et l'incident est journalisé.
"""

from __future__ import annotations

import hashlib
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.clientip import TrustedProxies
from app.core.errors import problem_response

log = logging.getLogger("nurselearn.ratelimit")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, per_minute: int, salt: str, trusted: TrustedProxies) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self.salt = salt
        self.trusted = trusted

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        redis = getattr(request.app.state, "redis", None)
        if self.per_minute <= 0 or redis is None or not request.url.path.startswith("/api/"):
            return await call_next(request)
        peer = request.client.host if request.client else None
        client = await self.trusted.client_address(peer, request.headers.get("x-forwarded-for"))
        fingerprint = hashlib.sha256(f"{self.salt}:{client}".encode()).hexdigest()[:16]
        now = time.time()
        key = f"nl:rl:{fingerprint}:{int(now // 60)}"
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, 70)
                count, _ = await pipe.execute()
        except Exception:  # fail-open : la disponibilité prime, l'incident est journalisé
            log.warning("ratelimit_indisponible")
            return await call_next(request)
        remaining = max(self.per_minute - int(count), 0)
        if count > self.per_minute:
            response = problem_response(
                request,
                429,
                "Too Many Requests",
                "Trop de requêtes, réessayez plus tard",
                "https://nurselearn.ai/problems/rate-limit",
            )
            response.headers["Retry-After"] = str(60 - int(now) % 60)
        else:
            response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(self.per_minute)
        response.headers["RateLimit-Remaining"] = str(remaining)
        return response

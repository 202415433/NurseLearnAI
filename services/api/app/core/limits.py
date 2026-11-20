"""Compteurs de limitation propres à l'authentification (fenêtres fixes, Redis partagé).

Complète la limite générale par minute (ratelimit.py) : inscription, connexion et envois de
courriels sont limités par adresse IP et par adresse courriel. Les clés sont des empreintes
(aucune adresse en clair dans Redis). Si Redis est indisponible, la requête passe (fail-open) :
le verrouillage des comptes en base protège toujours contre la force brute.
"""

from __future__ import annotations

import hashlib
import logging
import time

from starlette.requests import Request

log = logging.getLogger("nurselearn.limits")


async def within_limit(request: Request, bucket: str, subject: str, limit: int, window_seconds: int) -> bool:
    """Incrémente le compteur et renvoie False si la limite est dépassée."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None or limit <= 0:
        return True
    salt = request.app.state.settings.effective_rate_limit_salt
    digest = hashlib.sha256(f"{salt}:{bucket}:{subject.lower()}".encode()).hexdigest()[:24]
    key = f"nl:auth:{bucket}:{digest}:{int(time.time() // window_seconds)}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds + 10)
            count, _ = await pipe.execute()
    except Exception:
        log.warning("limite_auth_indisponible", extra={"bucket": bucket})
        return True
    return int(count) <= limit

"""Point d'entrée de l'API NurseLearn AI (santé, référentiels, catalogue, identité et accès)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.clientip import TrustedProxies
from app.core.config import API_VERSION, Settings, get_settings
from app.core.errors import install_error_handlers, problem_response
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.mailer import Mailer
from app.core.ratelimit import RateLimitMiddleware
from app.db.session import create_engine, create_sessionmaker
from app.domains.catalog.router import router as catalog_router
from app.domains.editorial.router import router as editorial_router
from app.domains.identity.organizations import router as organizations_router
from app.domains.identity.router import router as identity_router
from app.domains.media.attachments import router as attachments_router
from app.domains.media.delivery import router as delivery_router
from app.domains.media.quotas import router as media_quotas_router
from app.domains.media.routes import router as media_router
from app.domains.reference.router import router as reference_router

log = logging.getLogger("nurselearn.api")

DISCLAIMER = {
    "fr": "NurseLearn AI est une plateforme de formation. Ses attestations internes ne sont ni un diplôme, "
    "ni une licence, ni une autorisation d'exercer.",
    "en": "NurseLearn AI is a training platform. Its internal attestations are not a diploma, "
    "a licence or an authorization to practise.",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    """API principale (référentiels, catalogue, identité, édition, téléversement). Ne sert aucun octet."""
    app = _build_app(settings or get_settings(), "NurseLearn AI — API", "API de la plateforme NurseLearn AI. ")
    app.include_router(reference_router)
    app.include_router(catalog_router)
    app.include_router(identity_router)
    app.include_router(organizations_router)
    app.include_router(media_router)
    app.include_router(attachments_router)
    app.include_router(media_quotas_router)
    app.include_router(editorial_router)
    return app


def create_delivery_app(settings: Settings | None = None) -> FastAPI:
    """Service de DIFFUSION des médias sains (2.2.3-d), processus séparé : identité S3 de lecture seule
    (`nl-media-reader`), aucune route d'écriture. Lancement : uvicorn app.main:create_delivery_app --factory"""
    app = _build_app(
        settings or get_settings(),
        "NurseLearn AI — diffusion des médias",
        "Diffusion authentifiée des médias sains (aucune URL de stockage, aucun jeton autonome). ",
    )
    app.include_router(delivery_router)
    return app


def _build_app(settings: Settings, title: str, description: str) -> FastAPI:
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings.async_database_url)
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.redis = None
        if settings.redis_url:
            from redis.asyncio import Redis

            app.state.redis = Redis.from_url(settings.redis_url, socket_timeout=2)
        log.info("demarrage", extra={"env": settings.app_env, "version": API_VERSION})
        try:
            yield
        finally:
            if app.state.redis is not None:
                await app.state.redis.aclose()
            await app.state.engine.dispose()

    app = FastAPI(
        title=title,
        version=API_VERSION,
        description=description + DISCLAIMER["fr"],
        lifespan=lifespan,
        # Hors production seulement (sauf DOCS_ENABLED explicite) : n'expose pas la structure interne.
        docs_url="/docs" if settings.docs_visible else None,
        openapi_url="/openapi.json" if settings.docs_visible else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.mailer = Mailer(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept-Language", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Language", "RateLimit-Limit", "RateLimit-Remaining"],
        max_age=600,
    )
    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        salt=settings.effective_rate_limit_salt,
        trusted=TrustedProxies(settings.trusted_proxies),
    )
    app.add_middleware(RequestContextMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        # API JSON : rien à charger ni à afficher en cadre. /docs (Swagger UI) a besoin de ses ressources.
        if not request.url.path.startswith("/docs"):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        locale = getattr(request.state, "locale", None)
        if locale:
            response.headers["Content-Language"] = locale
        return response

    install_error_handlers(app)

    @app.get("/health", tags=["santé"], summary="Vivacité du processus")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": API_VERSION}

    @app.get("/ready", tags=["santé"], summary="Disponibilité des dépendances (PostgreSQL, Redis)")
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:  # noqa: BLE001 — l'état est rapporté, le détail journalisé
            log.exception("ready_database")
            checks["database"] = "error"
        if request.app.state.redis is None:
            checks["redis"] = "not_configured"
        else:
            try:
                await request.app.state.redis.ping()
                checks["redis"] = "ok"
            except Exception:  # noqa: BLE001
                log.exception("ready_redis")
                checks["redis"] = "error"
        if "error" in checks.values():
            return problem_response(
                request, 503, "Service Unavailable", "Dépendance indisponible", extra={"checks": checks}
            )
        # Région déclarée (ADR-0008) : information de configuration, pas une garantie de résidence.
        return JSONResponse({"status": "ready", "checks": checks, "region": settings.data_region})

    @app.get("/api/v1/about", tags=["santé"], summary="Mentions obligatoires")
    async def about() -> dict[str, object]:
        return {"name": "NurseLearn AI", "version": API_VERSION, "disclaimer": DISCLAIMER}

    return app


# Lancement : uvicorn app.main:create_app --factory  (la configuration est lue au démarrage)
#             uvicorn app.main:create_delivery_app --factory  (service de diffusion, 2.2.3-d)

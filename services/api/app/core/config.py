"""Configuration de l'API, lue exclusivement depuis l'environnement (voir .env.example)."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_VERSION = "0.3.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(..., description="postgresql+asyncpg://…")
    redis_url: str | None = None
    # Origines autorisées pour le navigateur (JSON dans l'environnement : ["http://localhost:3000"]).
    cors_origins: list[str] = ["http://localhost:3000"]
    default_locale: str = "fr"
    supported_locales: list[str] = ["fr", "en"]
    log_level: str = "INFO"
    # Requêtes /api/ autorisées par minute et par client (0 = désactivé). Nécessite Redis.
    rate_limit_per_minute: int = 300
    # Sel des empreintes de limitation de débit, identique pour toutes les instances.
    # Absent : dérivé de DATABASE_URL (stable pour un déploiement, jamais exposé).
    rate_limit_salt: str | None = None
    # Mandataires dont on accepte X-Forwarded-For : adresses, réseaux CIDR ou noms d'hôte
    # (JSON : ["web"] en local, l'adresse du reverse proxy en production). Vide = en-tête ignoré.
    trusted_proxies: list[str] = []
    # Documentation interactive (/docs, /openapi.json). Défaut : activée hors production.
    docs_enabled: bool | None = None

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production" and any(o.strip() == "*" for o in self.cors_origins):
            raise ValueError("CORS_ORIGINS ne peut pas contenir « * » en production")
        return self

    @property
    def docs_visible(self) -> bool:
        return self.docs_enabled if self.docs_enabled is not None else self.app_env != "production"

    @property
    def effective_rate_limit_salt(self) -> str:
        if self.rate_limit_salt:
            return self.rate_limit_salt
        return hashlib.sha256(f"nl-ratelimit:{self.database_url}".encode()).hexdigest()

    @property
    def async_database_url(self) -> str:
        """Garantit le pilote asyncpg, même si l'URL fournie vise psycopg."""
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix) :]
        return url

    @property
    def sync_database_url(self) -> str:
        """URL psycopg (synchrone), utilisée par le chargement des données de référence."""
        return self.async_database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

"""Configuration de l'API, lue exclusivement depuis l'environnement (voir .env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

API_VERSION = "0.2.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: str = "development"
    database_url: str = Field(..., description="postgresql+asyncpg://…")
    redis_url: str | None = None
    # Origines autorisées pour le navigateur (JSON dans l'environnement : ["http://localhost:3000"]).
    cors_origins: list[str] = ["http://localhost:3000"]
    default_locale: str = "fr"
    supported_locales: list[str] = ["fr", "en"]
    log_level: str = "INFO"

    @property
    def async_database_url(self) -> str:
        """Garantit le pilote asyncpg, même si l'URL fournie vise psycopg."""
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url

    @property
    def sync_database_url(self) -> str:
        """URL psycopg (synchrone), utilisée par le chargement des données de référence."""
        return self.async_database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

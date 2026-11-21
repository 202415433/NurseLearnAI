"""Configuration de l'API, lue exclusivement depuis l'environnement (voir .env.example)."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_VERSION = "0.4.0"


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

    # --- Identité et accès (Phase 2.1, ADR-0007) -------------------------------------------
    # Clé HMAC des empreintes de jetons (sessions, vérification, réinitialisation, invitations).
    # Obligatoire en production ; ailleurs, dérivée de DATABASE_URL si absente.
    token_hash_key: str | None = None
    session_idle_timeout_minutes: int = Field(120, ge=5, le=24 * 60)
    session_absolute_lifetime_hours: int = Field(168, ge=1, le=24 * 90)
    # Décision du 19/11/2026 : opérations sensibles refusées après 30 min d'inactivité.
    sensitive_idle_timeout_minutes: int = Field(30, ge=1, le=30)
    session_rotation_minutes: int = Field(15, ge=1, le=24 * 60)
    # Tolérance pour les requêtes parallèles portant encore l'identifiant qui vient d'être remplacé.
    session_reuse_grace_seconds: int = Field(60, ge=0, le=300)
    email_verification_ttl_hours: int = Field(24, ge=1, le=24 * 7)
    password_reset_ttl_minutes: int = Field(60, ge=10, le=24 * 60)
    invitation_ttl_hours: int = Field(72, ge=1, le=24 * 30)
    login_max_failures: int = Field(5, ge=3, le=20)
    login_lockout_minutes: int = Field(15, ge=1, le=24 * 60)
    # Limites propres à l'authentification (en plus de la limite générale par minute).
    register_per_hour_per_ip: int = 10
    login_per_10min_per_ip: int = 30
    email_actions_per_hour_per_address: int = 3
    email_actions_per_hour_per_ip: int = 10
    # Versions des documents acceptés à l'inscription (textes rédigés hors du code).
    terms_version: str = "2026-11-19"
    privacy_policy_version: str = "2026-11-19"
    # Courriel : « smtp » (Mailpit en local, aucun service payant), « memory » (tests), « disabled ».
    mail_backend: Literal["smtp", "memory", "disabled"] = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_starttls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None
    mail_from: str = "NurseLearn AI <no-reply@nurselearn.local>"
    # Adresse publique de l'interface, utilisée dans les liens des courriels.
    public_web_url: str = "http://localhost:3000"
    # Région d'hébergement déclarée (ADR-0008) ; déclarative, ne garantit aucune résidence.
    data_region: str | None = None
    # Publication RÉELLE d'un programme médical (étape 2.2.2-b) : désactivée tant que les conditions
    # de production ne sont pas réunies (MFA des relecteurs D9, relecteur qualifié D10, textes
    # juridiques). Les programmes non médicaux (démonstration, tests) restent publiables.
    medical_publication_enabled: bool = False

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production" and any(o.strip() == "*" for o in self.cors_origins):
            raise ValueError("CORS_ORIGINS ne peut pas contenir « * » en production")
        if self.app_env == "production" and (not self.token_hash_key or len(self.token_hash_key) < 32):
            raise ValueError("TOKEN_HASH_KEY (32 caractères ou plus) est obligatoire en production")
        return self

    @property
    def effective_token_hash_key(self) -> bytes:
        if self.token_hash_key:
            return self.token_hash_key.encode()
        return hashlib.sha256(f"nl-tokens:{self.database_url}".encode()).digest()

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

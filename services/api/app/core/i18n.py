"""Résolution de la langue et des textes traduisibles (colonnes *_i18n)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Query, Request

from app.core.config import get_settings


def negotiate_locale(accept_language: str | None, supported: list[str], default: str) -> str:
    """Choisit la langue à partir d'Accept-Language (q-values respectées)."""
    if not accept_language:
        return default
    candidates: list[tuple[float, str]] = []
    for part in accept_language.split(","):
        piece = part.strip()
        if not piece:
            continue
        lang, _, params = piece.partition(";")
        q = 1.0
        if params.strip().startswith("q="):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        if q > 0:  # q=0 signifie « non acceptable »
            candidates.append((q, lang.strip().lower()[:2]))
    for _, lang in sorted(candidates, key=lambda c: -c[0]):
        if lang in supported:
            return lang
    return default


def pick(text: dict[str, str] | None, locale: str, default: str = "fr") -> str | None:
    """Texte dans la langue demandée, sinon langue par défaut, sinon anglais, sinon le premier disponible."""
    if not text:
        return None
    for lang in (locale, default, "en", "fr"):
        value = text.get(lang)
        if value:
            return value
    return next((v for v in text.values() if v), None)


def get_locale(
    request: Request,
    lang: Annotated[str | None, Query(max_length=5, description="Force la langue (fr, en)")] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> str:
    settings = get_settings()
    if lang and lang.lower()[:2] in settings.supported_locales:
        locale = lang.lower()[:2]
    else:
        locale = negotiate_locale(accept_language, settings.supported_locales, settings.default_locale)
    request.state.locale = locale
    return locale

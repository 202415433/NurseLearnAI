"""Routeur `/api/v1/editorial` : agrège les routes éditoriales (étape 2.2.2-b).

Ordre : routes à chemin fixe d'abord, routes génériques `/{kind}/{id}/…` (relecture) en dernier.
"""

from fastapi import APIRouter

from app.domains.editorial import (
    routes_content,
    routes_lessons,
    routes_modules,
    routes_releases,
    routes_review,
    routes_sources,
)

router = APIRouter()
for module in (routes_content, routes_lessons, routes_modules, routes_releases, routes_sources, routes_review):
    router.include_router(module.router)

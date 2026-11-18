"""Référentiels publics : juridictions, métiers, spécialités (lecture seule)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import not_found
from app.core.i18n import get_locale, pick
from app.db.models import Jurisdiction, Profession, Specialty
from app.db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["référentiels"])

Session = Annotated[AsyncSession, Depends(get_session)]
Locale = Annotated[str, Depends(get_locale)]

# Ordre d'affichage : du métier d'assistance vers l'infirmier autorisé.
_CATEGORY_ORDER = {"care_assistant": 0, "practical_nurse": 1, "registered_nurse": 2, "other": 3}
_KIND_ORDER = {"province": 0, "state": 0, "region": 0, "country": 1, "framework": 2}


class JurisdictionOut(BaseModel):
    code: str
    name: str
    kind: str
    country_code: str | None
    parent_code: str | None
    default_locale: str


class ProfessionOut(BaseModel):
    code: str
    title: str
    category: str
    is_regulated: bool
    regulator_name: str | None
    regulator_url: str | None
    official_path: str | None
    validation_status: str
    jurisdiction_code: str


class SpecialtyOut(BaseModel):
    code: str
    group: str
    name: str


@router.get("/jurisdictions", response_model=list[JurisdictionOut], summary="Référentiels pédagogiques actifs")
async def list_jurisdictions(session: Session, locale: Locale) -> list[JurisdictionOut]:
    parent = aliased(Jurisdiction)
    rows = (await session.execute(
        select(Jurisdiction, parent.code)
        .outerjoin(parent, parent.id == Jurisdiction.parent_id)
        .where(Jurisdiction.is_active.is_(True))
    )).all()
    items = [JurisdictionOut(code=j.code, name=pick(j.name_i18n, locale) or j.code, kind=j.kind,
                             country_code=j.country_code, parent_code=parent_code, default_locale=j.default_locale)
             for j, parent_code in rows]
    return sorted(items, key=lambda i: (_KIND_ORDER.get(i.kind, 9), i.name))


@router.get("/jurisdictions/{code}/professions", response_model=list[ProfessionOut],
            summary="Métiers définis dans une juridiction (aucune équivalence implicite)")
async def list_professions(
    session: Session, locale: Locale,
    code: Annotated[str, Path(pattern=r"^[A-Za-z0-9-]{2,20}$")],
) -> list[ProfessionOut]:
    jurisdiction = (await session.execute(
        select(Jurisdiction).where(Jurisdiction.code == code.upper(), Jurisdiction.is_active.is_(True))
    )).scalar_one_or_none()
    if jurisdiction is None:
        raise not_found("Juridiction")
    professions = (await session.execute(
        select(Profession).where(Profession.jurisdiction_id == jurisdiction.id)
    )).scalars().all()
    items = [ProfessionOut(code=p.code, title=pick(p.title_i18n, locale) or p.code, category=p.category,
                           is_regulated=p.is_regulated, regulator_name=p.regulator_name,
                           regulator_url=p.regulator_url, official_path=pick(p.official_path_i18n, locale),
                           validation_status=p.validation_status, jurisdiction_code=jurisdiction.code)
             for p in professions]
    return sorted(items, key=lambda i: (_CATEGORY_ORDER.get(i.category, 9), i.title))


@router.get("/specialties", response_model=list[SpecialtyOut], summary="Spécialités du catalogue (groupes A à H)")
async def list_specialties(session: Session, locale: Locale) -> list[SpecialtyOut]:
    rows = (await session.execute(
        select(Specialty).where(Specialty.is_active.is_(True)).order_by(Specialty.sort_order, Specialty.code)
    )).scalars().all()
    return [SpecialtyOut(code=s.code, group=s.catalog_group, name=pick(s.name_i18n, locale) or s.code) for s in rows]

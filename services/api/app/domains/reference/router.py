"""Référentiels publics en lecture seule : pays, juridictions, métiers, spécialités.

Les listes de référence sont courtes (dizaines d'éléments) : elles sont filtrées
en base puis paginées en mémoire. Aucune donnée d'établissement n'est exposée ;
les compteurs de disponibilité ne portent que sur le catalogue public.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.i18n import get_locale, pick
from app.core.pagination import Page, PageParams, page_params
from app.db.models import Country, Jurisdiction, Profession, Program, Specialty
from app.db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["référentiels"])

Session = Annotated[AsyncSession, Depends(get_session)]
Locale = Annotated[str, Depends(get_locale)]
Paging = Annotated[PageParams, Depends(page_params)]

COUNTRY = r"^[A-Za-z]{2}$"
JURISDICTION = r"^[A-Za-z0-9-]{2,20}$"
PROFESSION = r"^[a-z0-9_]{1,60}$"
Category = Literal["care_assistant", "practical_nurse", "registered_nurse", "other"]
CATEGORIES = ("care_assistant", "practical_nurse", "registered_nurse", "other")
_CATEGORY_ORDER = {c: i for i, c in enumerate(CATEGORIES)}
_KIND_ORDER = {"country": 0, "province": 1, "state": 1, "region": 1, "framework": 2}


# --------------------------------------------------------------------------- schémas
class CountryOut(BaseModel):
    code: str
    name: str
    jurisdiction_count: int


class JurisdictionOut(BaseModel):
    code: str
    name: str
    kind: str
    country_code: str | None
    parent_code: str | None
    default_locale: str


class Availability(BaseModel):
    """Compteurs du catalogue PUBLIC uniquement ; aucun détail des brouillons n'est exposé."""
    published: int
    in_preparation: int


class ProfessionOut(BaseModel):
    code: str
    title: str
    category: str
    is_regulated: bool
    regulator_name: str | None
    regulator_url: str | None
    official_path: str | None
    validation_status: Literal["pending_expert_review", "validated", "needs_update"]
    jurisdiction_code: str
    availability: Availability


class SpecialtyOut(BaseModel):
    code: str
    group: str
    name: str
    published_programs: int


# --------------------------------------------------------------------------- utilitaires
def _public_program_filters():
    return (Program.organization_id.is_(None),)


async def _availability(session: AsyncSession, profession_ids: list[uuid.UUID]) -> dict[uuid.UUID, Availability]:
    if not profession_ids:
        return {}
    rows = (await session.execute(
        select(Program.profession_id,
               func.count().filter(Program.status == "published"),
               func.count().filter(Program.status == "draft"))
        .where(Program.profession_id.in_(profession_ids), *_public_program_filters())
        .group_by(Program.profession_id)
    )).all()
    found = {pid: Availability(published=pub, in_preparation=draft) for pid, pub, draft in rows}
    return {pid: found.get(pid, Availability(published=0, in_preparation=0)) for pid in profession_ids}


# --------------------------------------------------------------------------- routes
@router.get("/countries", response_model=Page[CountryOut], summary="Pays disposant d'au moins un référentiel actif")
async def list_countries(session: Session, locale: Locale, paging: Paging) -> Page[CountryOut]:
    rows = (await session.execute(
        select(Country, func.count(Jurisdiction.id))
        .join(Jurisdiction, (Jurisdiction.country_code == Country.code) & Jurisdiction.is_active.is_(True))
        .group_by(Country.code)
    )).all()
    items = sorted((CountryOut(code=c.code, name=pick(c.name_i18n, locale) or c.code, jurisdiction_count=n)
                    for c, n in rows), key=lambda i: i.name)
    return paging.slice(items)


@router.get("/jurisdictions", response_model=Page[JurisdictionOut], summary="Référentiels pédagogiques actifs")
async def list_jurisdictions(
    session: Session, locale: Locale, paging: Paging,
    country: Annotated[str | None, Query(pattern=COUNTRY, description="Code pays ISO (CA, FR, SN…)")] = None,
    scope: Annotated[Literal["all", "framework"], Query(description="framework : référentiels transnationaux")] = "all",
) -> Page[JurisdictionOut]:
    parent = aliased(Jurisdiction)
    query = (select(Jurisdiction, parent.code)
             .outerjoin(parent, parent.id == Jurisdiction.parent_id)
             .where(Jurisdiction.is_active.is_(True)))
    if country:
        query = query.where(Jurisdiction.country_code == country.upper())
    if scope == "framework":
        query = query.where(Jurisdiction.kind == "framework")
    rows = (await session.execute(query)).all()
    items = [JurisdictionOut(code=j.code, name=pick(j.name_i18n, locale) or j.code, kind=j.kind,
                             country_code=j.country_code, parent_code=parent_code, default_locale=j.default_locale)
             for j, parent_code in rows]
    items.sort(key=lambda i: (_KIND_ORDER.get(i.kind, 9), i.name))
    return paging.slice(items)


@router.get("/professions", response_model=Page[ProfessionOut],
            summary="Métiers, toujours rattachés à une juridiction (aucune équivalence implicite)")
async def list_professions(
    session: Session, locale: Locale, paging: Paging,
    jurisdiction: Annotated[str | None, Query(pattern=JURISDICTION)] = None,
    category: Annotated[Category | None, Query()] = None,
) -> Page[ProfessionOut]:
    query = (select(Profession, Jurisdiction.code)
             .join(Jurisdiction, Jurisdiction.id == Profession.jurisdiction_id)
             .where(Jurisdiction.is_active.is_(True)))
    if jurisdiction:
        query = query.where(Jurisdiction.code == jurisdiction.upper())
    if category:
        query = query.where(Profession.category == category)
    rows = (await session.execute(query)).all()
    availability = await _availability(session, [p.id for p, _ in rows])
    items = [ProfessionOut(code=p.code, title=pick(p.title_i18n, locale) or p.code, category=p.category,
                           is_regulated=p.is_regulated, regulator_name=p.regulator_name,
                           regulator_url=p.regulator_url, official_path=pick(p.official_path_i18n, locale),
                           validation_status=p.validation_status, jurisdiction_code=jcode,
                           availability=availability[p.id])
             for p, jcode in rows]
    items.sort(key=lambda i: (i.jurisdiction_code, _CATEGORY_ORDER.get(i.category, 9), i.title))
    return paging.slice(items)


@router.get("/specialties", response_model=Page[SpecialtyOut],
            summary="Spécialités (groupes A à H), avec le nombre de programmes publiés pour le filtre donné")
async def list_specialties(
    session: Session, locale: Locale, paging: Paging,
    group: Annotated[str | None, Query(pattern=r"^[A-Ha-h]$")] = None,
    jurisdiction: Annotated[str | None, Query(pattern=JURISDICTION)] = None,
    profession: Annotated[str | None, Query(pattern=PROFESSION)] = None,
) -> Page[SpecialtyOut]:
    counts_query = (select(Program.specialty_id, func.count())
                    .join(Profession, Profession.id == Program.profession_id)
                    .join(Jurisdiction, Jurisdiction.id == Profession.jurisdiction_id)
                    .where(Program.status == "published", *_public_program_filters())
                    .group_by(Program.specialty_id))
    if jurisdiction:
        counts_query = counts_query.where(Jurisdiction.code == jurisdiction.upper())
    if profession:
        counts_query = counts_query.where(Profession.code == profession)
    counts = dict((await session.execute(counts_query)).all())

    query = select(Specialty).where(Specialty.is_active.is_(True))
    if group:
        query = query.where(Specialty.catalog_group == group.upper())
    rows = (await session.execute(query.order_by(Specialty.sort_order, Specialty.code))).scalars().all()
    items = [SpecialtyOut(code=s.code, group=s.catalog_group, name=pick(s.name_i18n, locale) or s.code,
                          published_programs=counts.get(s.id, 0)) for s in rows]
    # Les spécialités ayant des programmes publiés pour ce parcours viennent en premier.
    items.sort(key=lambda i: (-min(i.published_programs, 1),))
    return paging.slice(items)

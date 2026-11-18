"""Catalogue public en lecture seule.

Règle de visibilité (non négociable) : seuls les programmes et modules au statut
`published` et n'appartenant à aucun établissement sont exposés ; une leçon n'est
listée que si elle possède une version PUBLIÉE — ce que la base n'autorise
qu'après relecture par un relecteur médical habilité, distinct de l'auteur.
Les brouillons et les contenus d'établissement ne sortent jamais par cette API.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import not_found
from app.core.i18n import get_locale, pick
from app.core.pagination import Page, PageParams, page_params
from app.db.models import Jurisdiction, Lesson, LessonVersion, Module, Profession, Program, ProgramModule, Specialty
from app.db.session import get_session
from app.domains.reference.router import JURISDICTION, PROFESSION

router = APIRouter(prefix="/api/v1", tags=["catalogue"])

Session = Annotated[AsyncSession, Depends(get_session)]
Locale = Annotated[str, Depends(get_locale)]
Paging = Annotated[PageParams, Depends(page_params)]
Level = Literal["beginner", "fundamental", "intermediate", "advanced", "professional_prep"]


def _public(model):  # publié ET hors établissement
    return (model.status == "published", model.organization_id.is_(None))


class ProgramSummary(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    summary: str | None
    level: str
    access_tier: str
    profession_code: str
    profession_title: str
    jurisdiction_code: str
    specialty_code: str | None
    specialty_name: str | None
    module_count: int


class LessonOut(BaseModel):
    code: str
    position: int
    kind: str
    estimated_minutes: int
    title: str
    locale: str
    jurisdiction_specific: bool
    next_review_due: str | None


class ModuleOut(BaseModel):
    code: str
    title: str
    summary: str | None
    level: str
    estimated_minutes: int
    position: int
    is_required: bool
    lessons: list[LessonOut]


class ProgramDetail(ProgramSummary):
    modules: list[ModuleOut]


def _base_query():
    module_count = (select(func.count()).select_from(ProgramModule)
                    .join(Module, Module.id == ProgramModule.module_id)
                    .where(ProgramModule.program_id == Program.id, *_public(Module))
                    .correlate(Program).scalar_subquery())
    return (select(Program, Profession, Jurisdiction.code, Specialty, module_count)
            .join(Profession, Profession.id == Program.profession_id)
            .join(Jurisdiction, Jurisdiction.id == Profession.jurisdiction_id)
            .outerjoin(Specialty, Specialty.id == Program.specialty_id)
            .where(*_public(Program)))


def _summary(row, locale: str) -> ProgramSummary:
    program, profession, jurisdiction_code, specialty, module_count = row
    return ProgramSummary(
        id=program.id, code=program.code, title=pick(program.title_i18n, locale) or program.code,
        summary=pick(program.summary_i18n, locale), level=program.level, access_tier=program.access_tier,
        profession_code=profession.code, profession_title=pick(profession.title_i18n, locale) or profession.code,
        jurisdiction_code=jurisdiction_code, specialty_code=specialty.code if specialty else None,
        specialty_name=pick(specialty.name_i18n, locale) if specialty else None, module_count=module_count or 0)


@router.get("/programs", response_model=Page[ProgramSummary], summary="Programmes publiés du catalogue public")
async def list_programs(
    session: Session, locale: Locale, paging: Paging,
    jurisdiction: Annotated[str | None, Query(pattern=JURISDICTION)] = None,
    profession: Annotated[str | None, Query(pattern=PROFESSION)] = None,
    specialty: Annotated[str | None, Query(pattern=r"^[a-z0-9_]{1,60}$")] = None,
    level: Annotated[Level | None, Query()] = None,
) -> Page[ProgramSummary]:
    filters = []
    if jurisdiction:
        filters.append(Jurisdiction.code == jurisdiction.upper())
    if profession:
        filters.append(Profession.code == profession)
    if specialty:
        filters.append(Specialty.code == specialty)
    if level:
        filters.append(Program.level == level)
    total = (await session.execute(
        select(func.count(Program.id))
        .join(Profession, Profession.id == Program.profession_id)
        .join(Jurisdiction, Jurisdiction.id == Profession.jurisdiction_id)
        .outerjoin(Specialty, Specialty.id == Program.specialty_id)
        .where(*_public(Program), *filters)
    )).scalar_one()
    rows = (await session.execute(
        _base_query().where(*filters).order_by(Program.code).limit(paging.limit).offset(paging.offset))).all()
    return Page(items=[_summary(r, locale) for r in rows], total=total, limit=paging.limit, offset=paging.offset)


@router.get("/programs/{program_id}", response_model=ProgramDetail, summary="Détail d'un programme publié")
async def get_program(session: Session, locale: Locale, program_id: uuid.UUID) -> ProgramDetail:
    row = (await session.execute(_base_query().where(Program.id == program_id))).first()
    if row is None:
        raise not_found("Programme")
    program, profession = row[0], row[1]

    modules = (await session.execute(
        select(Module, ProgramModule.position, ProgramModule.is_required)
        .join(ProgramModule, ProgramModule.module_id == Module.id)
        .where(ProgramModule.program_id == program.id, *_public(Module))
        .order_by(ProgramModule.position)
    )).all()

    default_locale = get_settings().default_locale
    module_ids = [m.id for m, _, _ in modules]
    versions = (await session.execute(
        select(Lesson, LessonVersion)
        .join(LessonVersion, LessonVersion.lesson_id == Lesson.id)
        .where(Lesson.module_id.in_(module_ids), LessonVersion.status == "published",
               LessonVersion.locale.in_(sorted({locale, default_locale})))
    )).all() if module_ids else []

    # Meilleure version publiée par leçon : langue demandée > juridiction du programme > contenu commun.
    best: dict[uuid.UUID, tuple[tuple[int, int], Lesson, LessonVersion]] = {}
    for lesson, version in versions:
        if version.jurisdiction_id not in (None, profession.jurisdiction_id):
            continue
        score = (int(version.locale == locale), int(version.jurisdiction_id is not None))
        if lesson.id not in best or score > best[lesson.id][0]:
            best[lesson.id] = (score, lesson, version)

    lessons_by_module: dict[uuid.UUID, list[LessonOut]] = {}
    for _, lesson, version in best.values():
        lessons_by_module.setdefault(lesson.module_id, []).append(LessonOut(
            code=lesson.code, position=lesson.position, kind=lesson.kind,
            estimated_minutes=lesson.estimated_minutes, title=version.title, locale=version.locale,
            jurisdiction_specific=version.jurisdiction_id is not None,
            next_review_due=version.next_review_due.isoformat() if version.next_review_due else None))

    module_out = [ModuleOut(code=m.code, title=pick(m.title_i18n, locale) or m.code, summary=pick(m.summary_i18n, locale),
                            level=m.level, estimated_minutes=m.estimated_minutes, position=pos, is_required=req,
                            lessons=sorted(lessons_by_module.get(m.id, []), key=lambda x: x.position))
                  for m, pos, req in modules]
    return ProgramDetail(**_summary(row, locale).model_dump(), modules=module_out)

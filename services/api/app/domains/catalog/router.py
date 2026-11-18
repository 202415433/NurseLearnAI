"""Catalogue public en lecture seule.

Règle de visibilité (non négociable) : seuls les programmes et modules au statut
`published`, n'appartenant à aucun établissement, sont exposés ; une leçon n'est
listée que si elle possède une version PUBLIÉE (donc relue par un relecteur
médical habilité — garanti par la base). Les brouillons ne sortent jamais.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import not_found
from app.core.i18n import get_locale, pick
from app.db.models import Jurisdiction, Lesson, LessonVersion, Module, Profession, Program, ProgramModule, Specialty
from app.db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["catalogue"])

Session = Annotated[AsyncSession, Depends(get_session)]
Locale = Annotated[str, Depends(get_locale)]
Code = Annotated[str, Path(pattern=r"^[a-z0-9_-]{1,80}$")]
LEVELS = ("beginner", "fundamental", "intermediate", "advanced", "professional_prep")


def _public(model):  # programmes et modules publiés, hors contenus privés d'établissement
    return (model.status == "published", model.organization_id.is_(None))


class ProgramSummary(BaseModel):
    code: str
    title: str
    summary: str | None
    level: str
    access_tier: str
    profession_code: str
    jurisdiction_code: str
    specialty_code: str | None


class ModuleSummary(BaseModel):
    code: str
    title: str
    summary: str | None
    level: str
    estimated_minutes: int
    access_tier: str
    position: int | None = None
    is_required: bool | None = None


class ProgramDetail(ProgramSummary):
    modules: list[ModuleSummary]


class LessonSummary(BaseModel):
    code: str
    position: int
    kind: str
    estimated_minutes: int
    title: str
    locale: str
    jurisdiction_specific: bool
    next_review_due: str | None


class ModuleDetail(ModuleSummary):
    specialty_code: str
    lessons: list[LessonSummary]


def _program_query():
    return (select(Program, Profession.code, Jurisdiction.code, Specialty.code)
            .join(Profession, Profession.id == Program.profession_id)
            .join(Jurisdiction, Jurisdiction.id == Profession.jurisdiction_id)
            .outerjoin(Specialty, Specialty.id == Program.specialty_id)
            .where(*_public(Program)))


def _program_summary(row, locale: str) -> ProgramSummary:
    program, profession_code, jurisdiction_code, specialty_code = row
    return ProgramSummary(code=program.code, title=pick(program.title_i18n, locale) or program.code,
                          summary=pick(program.summary_i18n, locale), level=program.level,
                          access_tier=program.access_tier, profession_code=profession_code,
                          jurisdiction_code=jurisdiction_code, specialty_code=specialty_code)


@router.get("/programs", response_model=list[ProgramSummary], summary="Programmes publiés")
async def list_programs(
    session: Session, locale: Locale,
    jurisdiction: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9-]{2,20}$")] = None,
    profession: Annotated[str | None, Query(pattern=r"^[a-z0-9_]{1,60}$")] = None,
    level: Annotated[str | None, Query(pattern="^(" + "|".join(LEVELS) + ")$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ProgramSummary]:
    query = _program_query()
    if jurisdiction:
        query = query.where(Jurisdiction.code == jurisdiction.upper())
    if profession:
        query = query.where(Profession.code == profession)
    if level:
        query = query.where(Program.level == level)
    rows = (await session.execute(query.order_by(Program.code).limit(limit))).all()
    return [_program_summary(r, locale) for r in rows]


@router.get("/programs/{code}", response_model=ProgramDetail, summary="Détail d'un programme publié")
async def get_program(session: Session, locale: Locale, code: Code) -> ProgramDetail:
    row = (await session.execute(_program_query().where(Program.code == code))).first()
    if row is None:
        raise not_found("Programme")
    modules = (await session.execute(
        select(Module, ProgramModule.position, ProgramModule.is_required)
        .join(ProgramModule, ProgramModule.module_id == Module.id)
        .where(ProgramModule.program_id == row[0].id, *_public(Module))
        .order_by(ProgramModule.position)
    )).all()
    summary = _program_summary(row, locale)
    return ProgramDetail(**summary.model_dump(), modules=[
        ModuleSummary(code=m.code, title=pick(m.title_i18n, locale) or m.code, summary=pick(m.summary_i18n, locale),
                      level=m.level, estimated_minutes=m.estimated_minutes, access_tier=m.access_tier,
                      position=pos, is_required=req)
        for m, pos, req in modules])


@router.get("/modules/{code}", response_model=ModuleDetail, summary="Détail d'un module publié")
async def get_module(
    session: Session, locale: Locale, code: Code,
    jurisdiction: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9-]{2,20}$")] = None,
) -> ModuleDetail:
    row = (await session.execute(
        select(Module, Specialty.code).join(Specialty, Specialty.id == Module.specialty_id)
        .where(Module.code == code, *_public(Module))
    )).first()
    if row is None:
        raise not_found("Module")
    module, specialty_code = row

    jurisdiction_id: uuid.UUID | None = None
    if jurisdiction:
        jurisdiction_id = (await session.execute(
            select(Jurisdiction.id).where(Jurisdiction.code == jurisdiction.upper()))).scalar_one_or_none()

    default_locale = get_settings().default_locale
    versions = (await session.execute(
        select(Lesson, LessonVersion)
        .join(LessonVersion, LessonVersion.lesson_id == Lesson.id)
        .where(Lesson.module_id == module.id, LessonVersion.status == "published",
               LessonVersion.locale.in_(sorted({locale, default_locale})))
    )).all()

    # Meilleure version publiée par leçon : langue demandée > juridiction exacte > contenu commun.
    best: dict[uuid.UUID, tuple[tuple[int, int], Lesson, LessonVersion]] = {}
    for lesson, version in versions:
        if version.jurisdiction_id not in (None, jurisdiction_id):
            continue
        score = (int(version.locale == locale), int(version.jurisdiction_id is not None))
        current = best.get(lesson.id)
        if current is None or score > current[0]:
            best[lesson.id] = (score, lesson, version)

    lessons = sorted((LessonSummary(code=l.code, position=l.position, kind=l.kind,
                                    estimated_minutes=l.estimated_minutes, title=v.title, locale=v.locale,
                                    jurisdiction_specific=v.jurisdiction_id is not None,
                                    next_review_due=v.next_review_due.isoformat() if v.next_review_due else None)
                      for _, l, v in best.values()), key=lambda s: s.position)
    return ModuleDetail(code=module.code, title=pick(module.title_i18n, locale) or module.code,
                        summary=pick(module.summary_i18n, locale), level=module.level,
                        estimated_minutes=module.estimated_minutes, access_tier=module.access_tier,
                        specialty_code=specialty_code, lessons=lessons)

"""Catalogue public en lecture seule, lu À TRAVERS L'ÉDITION PUBLIÉE (étape 2.2.2-c).

Règle de visibilité (non négociable) : un programme n'est exposé que s'il est `published`, hors
établissement, et que son édition publiée est complète et cohérente (`editions.py`). Modules,
ordre, caractère obligatoire, prérequis et versions exactes de leçon viennent de CETTE édition :
aucune autre version publiée n'est jamais choisie à sa place. Les brouillons, les éditions non
publiées et les contenus d'établissement ne sortent jamais par cette API.
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
from app.db.models import Jurisdiction, Lesson, LessonVersion, Module, Profession, Program, Specialty
from app.db.session import get_session
from app.domains.catalog.editions import RELEASE_CONTENT, RELEASE_PREREQUISITES, exposable_releases
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
    prerequisites: list[str]  # codes des modules prérequis, figés par l'édition
    lessons: list[LessonOut]


class ProgramDetail(ProgramSummary):
    release_no: int  # numéro de l'édition publiée servie
    modules: list[ModuleOut]


def _base_query():
    exposable = exposable_releases()
    return (
        select(
            Program,
            Profession,
            Jurisdiction.code,
            Specialty,
            exposable.c.module_count,
            exposable.c.release_id,
            exposable.c.release_no,
        )
        .join(exposable, exposable.c.program_id == Program.id)
        .join(Profession, Profession.id == Program.profession_id)
        .join(Jurisdiction, Jurisdiction.id == Profession.jurisdiction_id)
        .outerjoin(Specialty, Specialty.id == Program.specialty_id)
        .where(*_public(Program))
    )


def _summary(row, locale: str) -> ProgramSummary:
    program, profession, jurisdiction_code, specialty, module_count = row[:5]
    return ProgramSummary(
        id=program.id,
        code=program.code,
        title=pick(program.title_i18n, locale) or program.code,
        summary=pick(program.summary_i18n, locale),
        level=program.level,
        access_tier=program.access_tier,
        profession_code=profession.code,
        profession_title=pick(profession.title_i18n, locale) or profession.code,
        jurisdiction_code=jurisdiction_code,
        specialty_code=specialty.code if specialty else None,
        specialty_name=pick(specialty.name_i18n, locale) if specialty else None,
        module_count=module_count or 0,
    )


@router.get("/programs", response_model=Page[ProgramSummary], summary="Programmes publiés du catalogue public")
async def list_programs(
    session: Session,
    locale: Locale,
    paging: Paging,
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
    total = (
        await session.execute(select(func.count()).select_from(_base_query().where(*filters).subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            _base_query().where(*filters).order_by(Program.code).limit(paging.limit).offset(paging.offset)
        )
    ).all()
    return Page(items=[_summary(r, locale) for r in rows], total=total, limit=paging.limit, offset=paging.offset)


@router.get("/programs/{program_id}", response_model=ProgramDetail, summary="Détail d'un programme publié")
async def get_program(session: Session, locale: Locale, program_id: uuid.UUID) -> ProgramDetail:
    row = (await session.execute(_base_query().where(Program.id == program_id))).first()
    if row is None:
        raise not_found("Programme")
    release_id, release_no = row[5], row[6]
    default_locale = get_settings().default_locale

    content = (await session.execute(RELEASE_CONTENT, {"release": release_id})).mappings().all()
    module_ids = {c["module_id"] for c in content}
    lesson_ids = {c["lesson_id"] for c in content}
    modules = {m.id: m for m in (await session.execute(select(Module).where(Module.id.in_(module_ids)))).scalars()}
    lessons = {x.id: x for x in (await session.execute(select(Lesson).where(Lesson.id.in_(lesson_ids)))).scalars()}

    # Version EXACTE citée par l'édition : langue demandée si l'édition la cite, sinon langue de base.
    cited: dict[tuple, dict[str, uuid.UUID]] = {}
    placement: dict[uuid.UUID, tuple[int, bool]] = {}
    lesson_order: dict[tuple, int] = {}
    for c in content:
        key = (c["module_id"], c["lesson_id"])
        cited.setdefault(key, {})[c["locale"]] = c["lesson_version_id"]
        placement[c["module_id"]] = (c["module_position"], c["module_required"])
        lesson_order[key] = c["lesson_position"]
    # (la version française est garantie par la règle d'exposition ; jamais une version hors édition)
    chosen = {
        key: by_locale.get(locale) or by_locale.get(default_locale) or by_locale["fr"]
        for key, by_locale in cited.items()
    }
    versions = {
        v.id: v
        for v in (
            await session.execute(select(LessonVersion).where(LessonVersion.id.in_(set(chosen.values()))))
        ).scalars()
    }

    prerequisites: dict[uuid.UUID, list[str]] = {}
    for p in (await session.execute(RELEASE_PREREQUISITES, {"release": release_id})).mappings():
        prerequisites.setdefault(p["module_id"], []).append(modules[p["prerequisite_module_id"]].code)

    lessons_by_module: dict[uuid.UUID, list[LessonOut]] = {}
    for (module_id, lesson_id), version_id in chosen.items():
        lesson, version = lessons[lesson_id], versions[version_id]
        lessons_by_module.setdefault(module_id, []).append(
            LessonOut(
                code=lesson.code,
                position=lesson_order[(module_id, lesson_id)],
                kind=lesson.kind,
                estimated_minutes=lesson.estimated_minutes,
                title=version.title,
                locale=version.locale,
                jurisdiction_specific=version.jurisdiction_id is not None,
                next_review_due=version.next_review_due.isoformat() if version.next_review_due else None,
            )
        )

    module_out = [
        ModuleOut(
            code=modules[mid].code,
            title=pick(modules[mid].title_i18n, locale) or modules[mid].code,
            summary=pick(modules[mid].summary_i18n, locale),
            level=modules[mid].level,
            estimated_minutes=modules[mid].estimated_minutes,
            position=pos,
            is_required=req,
            prerequisites=sorted(prerequisites.get(mid, [])),
            lessons=sorted(lessons_by_module.get(mid, []), key=lambda x: x.position),
        )
        for mid, (pos, req) in sorted(placement.items(), key=lambda item: item[1][0])
    ]
    return ProgramDetail(**_summary(row, locale).model_dump(), release_no=release_no, modules=module_out)

"""Pagination par limite/décalage, enveloppe de réponse commune."""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

MAX_LIMIT = 100
MAX_OFFSET = 10_000


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    limit: int
    offset: int

    def slice(self, items: list[T]) -> Page[T]:
        return Page(
            items=items[self.offset : self.offset + self.limit], total=len(items), limit=self.limit, offset=self.offset
        )


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Nombre d'éléments (1 à 100)")] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET, description="Décalage")] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)

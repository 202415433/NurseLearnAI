"""Schéma de contenu `blocks/v1` des versions de leçon.

Union discriminée par `type`. Aucun HTML : texte brut avec un balisage minimal (`**gras**`,
`*italique*`, `[libellé](lien)`), liens `https://` ou internes (`/…`) uniquement ; `javascript:`,
`data:`, balises et caractères de contrôle refusés. Les identifiants de bloc sont stables (reprise
de lecture, signalements, comparaison) et uniques dans la version.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BLOCK_ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"
MAX_BLOCKS = 300
MAX_BODY_BYTES = 256 * 1024
_TAG = re.compile(r"<\s*/?\s*[a-zA-Z!?]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
_BAD_SCHEME = re.compile(r"(?i)\b(javascript|data|vbscript|file)\s*:")


def check_text(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("caractères de contrôle interdits")
    if _TAG.search(value):
        raise ValueError("HTML interdit")
    if _BAD_SCHEME.search(value):
        raise ValueError("schéma de lien interdit")
    for _, target in _LINK.findall(value):
        if not (target.startswith("https://") or (target.startswith("/") and not target.startswith("//"))):
            raise ValueError("lien autorisé : https:// ou chemin interne")
    return value


Text = Annotated[str, Field(min_length=1, max_length=4000)]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: Annotated[str, Field(pattern=BLOCK_ID)]


class Heading(_Block):
    type: Literal["heading"]
    level: Annotated[int, Field(ge=2, le=4)]
    text: Annotated[str, Field(min_length=1, max_length=200)]

    _t = field_validator("text")(check_text)


class Paragraph(_Block):
    type: Literal["paragraph"]
    text: Text

    _t = field_validator("text")(check_text)


class ListBlock(_Block):
    type: Literal["list"]
    ordered: bool = False
    items: Annotated[list[ShortText], Field(min_length=1, max_length=50)]

    @field_validator("items")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        return [check_text(x) for x in v]


class Table(_Block):
    type: Literal["table"]
    headers: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(min_length=1, max_length=10)]
    rows: Annotated[list[list[Annotated[str, Field(max_length=500)]]], Field(max_length=50)]

    @model_validator(mode="after")
    def _shape(self) -> Table:
        for cell in [*self.headers, *(c for r in self.rows for c in r)]:
            check_text(cell) if cell else None
        if any(len(r) != len(self.headers) for r in self.rows):
            raise ValueError("chaque ligne doit avoir autant de cellules que d'en-têtes")
        return self


class Callout(_Block):
    type: Literal["callout"]
    tone: Literal["info", "important", "safety"]
    text: Text

    _t = field_validator("text")(check_text)


class Definition(_Block):
    type: Literal["definition"]
    term: Annotated[str, Field(min_length=1, max_length=200)]
    text: Text

    _t = field_validator("term", "text")(check_text)


class Steps(_Block):
    type: Literal["steps"]
    items: Annotated[list[ShortText], Field(min_length=1, max_length=30)]

    @field_validator("items")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        return [check_text(x) for x in v]


class KeyPoints(_Block):
    type: Literal["key_points"]
    items: Annotated[list[ShortText], Field(min_length=1, max_length=10)]

    @field_validator("items")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        return [check_text(x) for x in v]


class Media(_Block):
    type: Literal["image", "video", "document"]
    resource_id: uuid.UUID


class Reference(_Block):
    type: Literal["reference"]
    reference_id: uuid.UUID
    locator: Annotated[str | None, Field(max_length=200)] = None


Block = Annotated[
    Heading | Paragraph | ListBlock | Table | Callout | Definition | Steps | KeyPoints | Media | Reference,
    Field(discriminator="type"),
]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: Literal["blocks/v1"] = Field("blocks/v1", alias="schema")
    blocks: Annotated[list[Block], Field(max_length=MAX_BLOCKS)]

    @model_validator(mode="after")
    def _unique_ids(self) -> Body:
        ids = [b.id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("identifiants de bloc en double")
        return self

    def as_json(self) -> dict:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return data

    @property
    def needs_source(self) -> bool:
        """Procédure ou encadré de sécurité : une source citée est exigée avant soumission."""
        return any(b.type == "steps" or (b.type == "callout" and b.tone == "safety") for b in self.blocks)

    @property
    def reference_ids(self) -> set[uuid.UUID]:
        return {b.reference_id for b in self.blocks if isinstance(b, Reference)}

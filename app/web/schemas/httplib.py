"""KiCad's HTTP library, and the tokens it is read with, over the API.

The first four shapes are KiCad's, not this app's: they are what its HTTP
library expects, named as it names them, and every value is a string.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.auth.tokens import MAX_LABEL, Token
from app.services.httplib import HttpCategory, HttpPart


class HttpRoot(BaseModel):
    """What KiCad asks first, to see that the address is a library: the
    endpoints there are. Only the keys are looked at."""

    categories: str
    parts: str


class HttpCategoryOut(BaseModel):
    id: str
    name: str
    description: str

    @classmethod
    def of(cls, category: HttpCategory) -> HttpCategoryOut:
        return cls(id=category.id, name=category.name, description=category.description)


class HttpField(BaseModel):
    value: str
    visible: str  # "True" or "False": KiCad wants strings


class HttpPartOut(BaseModel):
    id: str
    name: str
    symbolIdStr: str  # KiCad's name for it
    description: str
    keywords: str
    fields: dict[str, HttpField]

    @classmethod
    def of(cls, part: HttpPart) -> HttpPartOut:
        return cls(
            id=part.id,
            name=part.name,
            symbolIdStr=part.symbol,
            description=part.description,
            keywords=part.keywords,
            fields={
                name: HttpField(value=value, visible=str(visible))
                for name, (value, visible) in part.fields.items()
            },
        )


# -- tokens -----------------------------------------------------------------


class TokenBody(BaseModel):
    # What it is for, to know it again in the list: "Laptop", "Lab PC".
    label: str = Field("", max_length=MAX_LABEL)


class TokenOut(BaseModel):
    id: int
    label: str
    owner: str
    created_at: float  # epoch seconds
    last_used_at: float | None
    # Whether it is the asker's own: a librarian is shown everyone's.
    yours: bool

    @classmethod
    def of(cls, token: Token, *, asker: str) -> TokenOut:
        return cls(
            id=token.id,
            label=token.label,
            owner=token.owner,
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            yours=token.subject == asker,
        )


class TokenList(BaseModel):
    tokens: list[TokenOut]


class TokenCreated(BaseModel):
    """A new token. `secret` is shown this once and is not kept."""

    token: TokenOut
    secret: str
    # The .kicad_httplib file with the token in it, ready to save.
    library_file: dict[str, object]


class TokenRevoked(BaseModel):
    id: int

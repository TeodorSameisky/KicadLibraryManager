"""The library as KiCad's HTTP library reads it.

KiCad 8 and later can take a library from a REST API: a `.kicad_httplib`
file names an address and a token, and the symbol chooser then lists the
categories it is given as libraries and their parts under them. What it is
given is a part's data only. The symbol and footprint a part names are looked
up in the designer's own library tables, so they have to have the library's
repository checked out, under the same library names -- which is what its
`sym-lib-table` and `fp-lib-table` set up.

A part carries the same fields however it reaches the sheet: these are the
ones the Remote Symbols panel places, from `part_fields`. The difference is
the footprint. The panel sends it and KiCad files it under a library of its
own; here it is the library's own name for it, which the designer has.

Retired parts are not offered: the chooser cannot show that one is retired,
and nothing new should be drawn with it. A draft is, marked as one, since the
catalog offers drafts too. A part that names no symbol is left out: there is
nothing the chooser could place.

KiCad wants every value as a string, booleans included.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.parts import Part, PartStatus
from app.domain.refs import LibraryRef
from app.library.placement import PlacementOptions, part_fields
from app.library.snapshot import Snapshot

API_VERSION = "v1"

# KiCad's own fields, under the names its HTTP library gives them.
_KICAD_NAMES = {
    "Value": "value",
    "Footprint": "footprint",
    "Datasheet": "datasheet",
    "Description": "description",
}
# Shown on the sheet. The rest are kept on the symbol, hidden, as the panel
# places them.
_VISIBLE = {"Value"}


@dataclass(frozen=True)
class HttpPart:
    id: str
    name: str
    symbol: str
    description: str
    keywords: str
    fields: dict[str, tuple[str, bool]] = field(default_factory=dict)  # value, visible


@dataclass(frozen=True)
class HttpCategory:
    id: str
    name: str
    description: str


def offered(part: Part) -> bool:
    return part.status != PartStatus.OBSOLETE and LibraryRef.parse(part.symbol) is not None


def _description(part: Part) -> str:
    if part.status == PartStatus.DRAFT:
        return f"Draft: {part.description}".strip()
    return part.description


def http_part(part: Part, snapshot: Snapshot, options: PlacementOptions) -> HttpPart:
    fields = part_fields(part, snapshot, options, received=False)
    fields["Description"] = _description(part)

    words: list[str] = []
    for ref in part.mpns:
        words.append(ref.mpn)
        mpn = snapshot.mpn(ref.mpn)
        if mpn is not None and mpn.manufacturer and mpn.manufacturer not in words:
            words.append(mpn.manufacturer)

    return HttpPart(
        id=part.ipn,
        name=part.ipn,
        symbol=part.symbol,
        description=_description(part),
        keywords=" ".join(words),
        fields={
            _KICAD_NAMES.get(name, name): (value, name in _VISIBLE)
            for name, value in fields.items()
        },
    )


def categories(snapshot: Snapshot) -> list[HttpCategory]:
    """The categories that have a part to offer, each shown as a library."""
    codes = sorted({part.category for part in snapshot.parts.parts.values() if offered(part)})
    return [
        HttpCategory(
            id=code,
            name=f"{code} {snapshot.category_name(code)}".strip(),
            description=snapshot.category_name(code),
        )
        for code in codes
        if code
    ]


def parts_in(snapshot: Snapshot, category: str, options: PlacementOptions) -> list[HttpPart]:
    """A category's parts, each in full: KiCad needs every field of every part
    to fill its chooser, and asks for them one by one if it is not given them."""
    return [
        http_part(part, snapshot, options)
        for part in sorted(snapshot.parts.parts.values(), key=lambda part: part.ipn)
        if part.category == category and offered(part)
    ]


def part(snapshot: Snapshot, ipn: str, options: PlacementOptions) -> HttpPart | None:
    found = snapshot.part(ipn)
    if found is None or not offered(found):
        return None
    return http_part(found, snapshot, options)


def library_file(*, name: str, root_url: str, token: str) -> dict[str, object]:
    """A `.kicad_httplib` file, for KiCad's symbol library table.

    The timeouts are how long KiCad keeps what it was told: a part for a
    minute, as KiCad suggests, and the categories for ten.
    """
    return {
        "meta": {"version": 1.0},
        "name": name,
        "description": f"Parts from {name}",
        "source": {
            "type": "REST_API",
            "api_version": API_VERSION,
            "root_url": root_url,
            "token": token,
            "timeout_parts_seconds": 60,
            "timeout_categories_seconds": 600,
        },
    }

"""Telling when a part is one the library already has.

With no ERP behind it, this library is the part master, and the classic way a
part master decays is the same part under two numbers: two people needing a
10k 0603 resistor a month apart, neither finding the other's. So a part being
asked for is held up against the parts there are, and the library's own parts
against each other.

Two parts are taken to be alike when they would be bought as the same thing,
or would be placed as the same thing:

- **They list the same manufacturer part.** One manufacturer part may serve
  several internal parts -- that is why they are files of their own -- but a
  person asking for a part should know which numbers it already serves.
- **They are in one category, drawn with one footprint, and every field both
  carry says the same.** `4k7` and `4.7k` say the same; `1%` and `5%` do not.
  A field only one of them carries is not held against them: a part asked for
  with less said about it is still the same part.
- **They are described the same way.**

None of this refuses anything. A part that looks like another may be meant to
differ in what nobody wrote down; whether it does is a person's to say.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.parts import Part, PartStatus
from app.domain.quantities import magnitude

# As many alike parts as are worth reading through before a request is sent.
LIMIT = 10


# -- what a value says ------------------------------------------------------

_SPACE = re.compile(r"\s+")


def _text(value: str) -> str:
    return _SPACE.sub(" ", value).strip().casefold()


def value_key(value: str) -> tuple[str, str]:
    """What a field's value says, so that two ways of writing it compare equal.

    A value that is one amount -- `4k7`, `4.7 kΩ`, `0.1W`, `100mW` -- is the
    number it stands for. Anything more than that is compared as text, case and
    spacing aside: `100nF 50V X7R` and `100nF 16V X7R` are not the same
    capacitor, though the first amount in each is.
    """
    text = _text(value)
    words = text.split(" ")
    single = len(words) == 1 or (
        len(words) == 2 and re.fullmatch(r"[+-]?[\d.,]+", words[0]) is not None
    )
    amount = magnitude(value) if single else None
    if amount is not None and math.isfinite(amount):
        return ("amount", f"{amount:.9g}")
    return ("text", text)


def same_value(a: str, b: str) -> bool:
    return value_key(a) == value_key(b)


def _fields(fields: Mapping[str, str]) -> dict[str, str]:
    """A part's fields with something in them, by name regardless of case."""
    return {name.strip().casefold(): value for name, value in fields.items() if value.strip()}


# -- a part being asked for ---------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A part as it is being asked for or edited: what is held up against
    the library."""

    category: str = ""
    description: str = ""
    footprint: str = ""
    fields: Mapping[str, str] | None = None
    mpns: Sequence[str] = ()
    # The part being edited, which is not a duplicate of itself.
    ipn: str = ""


@dataclass(frozen=True)
class Alike:
    """A part in the library, and why it looks like the one being asked for."""

    part: Part
    reasons: tuple[str, ...]
    # How strongly: a shared manufacturer part outweighs any number of fields.
    weight: int


def alike(candidate: Candidate, parts: Iterable[Part], limit: int = LIMIT) -> list[Alike]:
    """The parts in the library that look like `candidate`, most alike first."""
    wanted_mpns = {mpn.strip().casefold(): mpn.strip() for mpn in candidate.mpns if mpn.strip()}
    wanted_fields = _fields(candidate.fields or {})
    wanted_description = _text(candidate.description)
    footprint = candidate.footprint.strip()

    found: list[Alike] = []
    for part in parts:
        if part.ipn == candidate.ipn:
            continue
        reasons: list[str] = []
        weight = 0

        shared = [
            wanted_mpns[ref.mpn.casefold()]
            for ref in part.mpns
            if ref.mpn.casefold() in wanted_mpns
        ]
        if shared:
            reasons.append(f"also lists {', '.join(shared)}")
            weight += 1000 * len(shared)

        same_drawing = part.category == candidate.category and part.footprint == footprint
        if candidate.category and same_drawing:
            theirs = _fields(part.fields)
            both = sorted(set(wanted_fields) & set(theirs))
            if both and all(same_value(wanted_fields[name], theirs[name]) for name in both):
                names = [_display_name(part.fields, name) for name in both]
                where = f"footprint {footprint}" if footprint else "no footprint"
                reasons.append(f"same {where} and the same {_listed(names)}")
                weight += 10 * len(both)

        if wanted_description and _text(part.description) == wanted_description:
            reasons.append("same description")
            weight += 5

        if reasons:
            found.append(Alike(part, tuple(reasons), weight))

    found.sort(key=lambda each: (-each.weight, each.part.ipn))
    return found[:limit]


def _display_name(fields: Mapping[str, str], key: str) -> str:
    return next((name for name in fields if name.strip().casefold() == key), key)


def _listed(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


# -- the library against itself -------------------------------------------------


@dataclass(frozen=True)
class Duplicate:
    """A part that looks the same as one numbered before it."""

    part: Part
    earlier: Part
    reason: str


def duplicates(parts: Iterable[Part]) -> list[Duplicate]:
    """Parts the library has twice, each reported against the first of them.

    Stricter than `alike`, because each is an entry on the issues page and
    not a hint beside a form: two parts are the same here only if they carry
    the same fields, all of them, saying the same thing, in one category with
    one footprint -- or prefer the same manufacturer part within a category.
    A retired part is left out: a replacement looking like what it replaced is
    the point of it.
    """
    live = sorted(
        (part for part in parts if part.status != PartStatus.OBSOLETE and part.category),
        key=lambda part: part.ipn,
    )
    first_by_fields: dict[tuple[object, ...], Part] = {}
    first_by_preferred: dict[tuple[str, str], Part] = {}
    found: list[Duplicate] = []
    reported: set[str] = set()

    for part in live:
        fields = _fields(part.fields)
        if fields:
            key = (
                part.category,
                part.footprint,
                tuple(sorted((name, value_key(value)) for name, value in fields.items())),
            )
            earlier = first_by_fields.setdefault(key, part)
            if earlier is not part:
                found.append(Duplicate(part, earlier, "same footprint and the same fields"))
                reported.add(part.ipn)
                continue

        preferred = next((ref.mpn for ref in part.mpns if ref.preferred), None)
        if preferred:
            earlier = first_by_preferred.setdefault((part.category, preferred.casefold()), part)
            if earlier is not part and part.ipn not in reported:
                found.append(Duplicate(part, earlier, f"both prefer {preferred}"))
    return found

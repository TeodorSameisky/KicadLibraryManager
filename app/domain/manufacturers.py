"""Who makes a manufacturer part, under one name.

The same company is written "Texas Instruments", "TI" and "Texas Instruments
Inc." by three people on three days. To a person those are one manufacturer;
to anything that looks a part up by who makes it -- a supplier's catalogue
above all -- they are three, and two of them find nothing.

So a library may say which manufacturers it knows, each under the one name it
is to be called by, with the other ways it gets written. A name is then
recognised however it was typed, and shown, searched and written down as the
one name.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

# How a company is incorporated, which is not part of what it is called:
# "Yageo Corp." and "Yageo" are one manufacturer. Only these: a word like
# "Semiconductor" or "Electronics" does tell two companies apart.
_LEGAL_FORMS = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
        "llc", "lp", "plc", "gmbh", "ag", "kg", "se", "sa", "sas", "nv", "bv", "ab", "as",
        "oy", "spa", "srl", "kk", "pte", "pty",
    }
)  # fmt: skip

_NOT_A_WORD = re.compile(r"[^\w]+", re.UNICODE)


def name_key(text: str) -> str:
    """What two spellings of one name have in common.

    Case, accents, punctuation and spacing are dropped, "&" is read as "and",
    and the legal form at the end goes: "YAGEO Corp.", "Yageo corporation"
    and "yageo" all come to "yageo", and "Würth" to "wurth". Two names with
    the same key are taken for the same manufacturer; two with different keys
    are not, however alike they look -- which is what a list of aliases is
    for.
    """
    # Taken apart, so that an accent is a mark of its own and can be left out:
    # the same name is typed with and without, depending on the keyboard.
    apart = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in apart if not unicodedata.combining(ch))
    # A full stop abbreviates and does not separate: "N.V." is one word, as
    # "Corp." is, not two letters.
    folded = plain.casefold().replace(".", "").replace("&", " and ")
    words = [word for word in _NOT_A_WORD.split(folded) if word]
    # From the end, and never the whole name: a company called "AG" is one.
    while len(words) > 1 and (words[-1] in _LEGAL_FORMS or words[-1] == "and"):
        words.pop()
    return " ".join(words)


@dataclass(frozen=True)
class Manufacturer:
    name: str  # what it is called, everywhere
    aliases: tuple[str, ...] = ()  # the other ways it gets written

    @property
    def spellings(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class Manufacturers:
    """The manufacturers a library knows. Empty when it does not say."""

    known: tuple[Manufacturer, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.known)

    @cached_property
    def _by_key(self) -> dict[str, Manufacturer]:
        found: dict[str, Manufacturer] = {}
        for manufacturer in self.known:
            for spelling in manufacturer.spellings:
                # The first to claim a spelling keeps it.
                found.setdefault(name_key(spelling), manufacturer)
        return found

    def resolve(self, text: str) -> Manufacturer | None:
        """The manufacturer a name means, however it was typed, or None."""
        return self._by_key.get(name_key(text)) if text.strip() else None

    def canonical(self, text: str) -> str:
        """The one name for a manufacturer, or the name as given if it is not
        one this library knows."""
        found = self.resolve(text)
        return found.name if found else text.strip()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(manufacturer.name for manufacturer in self.known)


def spelling_groups(names: Iterable[str]) -> list[tuple[str, ...]]:
    """Names that are one manufacturer written more than one way.

    Each group is the different spellings found for one name, the most used
    first. For a library that keeps no list of manufacturers: it is where
    such a list starts.
    """
    counts: dict[str, dict[str, int]] = {}
    for name in names:
        written = name.strip()
        if written:
            spellings = counts.setdefault(name_key(written), {})
            spellings[written] = spellings.get(written, 0) + 1
    return [
        tuple(sorted(spellings, key=lambda spelling: (-spellings[spelling], spelling)))
        for _, spellings in sorted(counts.items())
        if len(spellings) > 1
    ]

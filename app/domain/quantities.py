"""A value as an engineer writes it, as a number.

"4k7", "100nF", "0.1 W", "1/4W", "1%". A part's fields are text, written by
hand, and text sorts 100k before 4k7 and cannot say whether 16V is at least
10V. `magnitude` reads the number such a value stands for, so that values can
be put in order and compared.

It reads how much, and not of what: the unit is looked at only far enough to
tell a prefix from it. That is enough here, because the values compared are
always one field's -- resistances with resistances -- and it means "10k" and
"10 kΩ" are the same amount without a table of units to keep.

What is not such a value has no magnitude: "X7R", "1N4148", "SOT-23".
"""

from __future__ import annotations

import re

_PREFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,  # the micro sign
    "μ": 1e-6,  # and the Greek letter, which looks the same and is not
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,  # as resistors are marked
    "M": 1e6,
    "G": 1e9,
}

# What may follow the number: a unit, with or without a prefix before it.
_UNIT = re.compile(r"^[A-Za-zµμΩ°%/]*$")

# "4k7", "0R1", "3V3": the prefix or the unit standing where the point would.
_INFIX = re.compile(r"^(\d+)([pnuµμmkKMGRrVv])(\d+)([A-Za-zΩ]*)$")
# "R47": the same, with nothing before it.
_LEADING_R = re.compile(r"^[Rr](\d+)$")
_FRACTION = re.compile(r"^(\d+)/(\d+)(.*)$")
_NUMBER = re.compile(r"^([+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)(.*)$")


def _scale(unit: str) -> float | None:
    """What a number is multiplied by for the unit after it, or None if what
    follows is not a unit at all."""
    if not _UNIT.match(unit):
        return None
    # A prefix letter alone is a prefix -- "10k", "100n", "10m" -- and so is
    # one before a unit. Nothing else is: the V of "5V" is the whole unit.
    return _PREFIXES.get(unit[:1], 1.0)


def magnitude(text: str) -> float | None:
    """The number a value stands for, or None if it is not a quantity.

    Only the first of several is read: "100nF 50V X7R" is 100 n.
    """
    words = text.replace("+/-", "").replace("±", "").split()
    if not words:
        return None
    word = words[0]
    # "10 kΩ": the unit written apart from its number.
    if len(words) > 1 and re.fullmatch(r"[+-]?[\d.,]+", word) and _UNIT.match(words[1]):
        word += words[1]

    if match := _INFIX.match(word):
        whole, mark, part, _unit = match.groups()
        return float(f"{whole}.{part}") * _PREFIXES.get(mark, 1.0)
    if match := _LEADING_R.match(word):
        return float(f"0.{match.group(1)}")
    if match := _FRACTION.match(word):
        over, under, unit = match.groups()
        scale = _scale(unit)
        if scale is None or int(under) == 0:
            return None
        return int(over) / int(under) * scale
    if match := _NUMBER.match(word):
        number, unit = match.groups()
        scale = _scale(unit)
        if scale is None:
            return None
        return float(number.replace(",", ".")) * scale
    return None


def in_order(values: list[str]) -> list[str]:
    """Values smallest first; those that are not quantities after them, by
    their letters."""

    def place(value: str) -> tuple[int, float, str]:
        amount = magnitude(value)
        return (1, 0.0, value.casefold()) if amount is None else (0, amount, value.casefold())

    return sorted(values, key=place)

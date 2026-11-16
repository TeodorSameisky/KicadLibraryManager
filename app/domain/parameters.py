"""What the parts of a category can be told apart by, and narrowing to it.

A resistor is chosen by its resistance, its tolerance and its package, not by
its number. A *parameter* is one such thing a part says about itself: a field,
most of all. A *condition* asks something of one parameter -- that it is 10k,
that it is at least 25V -- and a list of parts is narrowed by the conditions
put to it.

Several conditions on one parameter that say what it *is* are a choice: 0402
or 0603. Everything else has to hold together.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.quantities import magnitude


class Op(StrEnum):
    IS = "="
    AT_LEAST = ">="
    AT_MOST = "<="


# The first of these in a condition is where the name ends.
_OP = re.compile(r">=|<=|=")


@dataclass(frozen=True)
class Condition:
    """Something asked of one parameter."""

    name: str
    op: Op
    value: str

    @classmethod
    def parse(cls, text: str) -> Condition:
        """A condition as it is written: "Resistance=10k", "Voltage>=25V".

        Raises `ValueError`, with a sentence, for one that says nothing. A
        name with one of the signs in it cannot be asked for this way.
        """
        found = _OP.search(text)
        if found is None:
            raise ValueError(f"{text!r} is not a condition: it needs =, >= or <=.")
        name, value = text[: found.start()].strip(), text[found.end() :].strip()
        op = Op(found.group())
        if not name:
            raise ValueError(f"{text!r} does not say which parameter.")
        if not value:
            raise ValueError(f"{name} is asked for without a value.")
        if op is not Op.IS and magnitude(value) is None:
            raise ValueError(f"{name} {op.value} {value}: {value!r} is not a quantity.")
        return cls(name, op, value)


def _reaches(amount: float, op: Op, bound: float) -> bool:
    # 4.7 k is not exactly 4700 once it has been multiplied out.
    if math.isclose(amount, bound, rel_tol=1e-9, abs_tol=1e-18):
        return True
    return amount > bound if op is Op.AT_LEAST else amount < bound


def satisfied(values: Sequence[str], conditions: Iterable[Condition]) -> bool:
    """Whether what a part says for one parameter meets every condition put
    to that parameter. `values` is all it says: usually one, none if it says
    nothing, several where it has more than one answer."""
    wanted = set()
    for condition in conditions:
        if condition.op is Op.IS:
            wanted.add(condition.value)
            continue
        bound = magnitude(condition.value)
        amounts = (magnitude(value) for value in values)
        if bound is None or not any(
            amount is not None and _reaches(amount, condition.op, bound) for amount in amounts
        ):
            return False
    return not wanted or bool(wanted.intersection(values))


def by_parameter(conditions: Iterable[Condition]) -> dict[str, tuple[Condition, ...]]:
    """The conditions, grouped by the parameter each is put to."""
    grouped: dict[str, list[Condition]] = {}
    for condition in conditions:
        grouped.setdefault(condition.name, []).append(condition)
    return {name: tuple(found) for name, found in grouped.items()}


@dataclass(frozen=True)
class Choice:
    """One value a parameter can be narrowed to, and how many parts it
    would leave."""

    value: str
    count: int


@dataclass(frozen=True)
class Parameter:
    name: str
    # Whether every value is a quantity, so that "at least" and "at most"
    # mean something for it.
    numeric: bool
    choices: tuple[Choice, ...]

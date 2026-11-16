"""Finding parts: by words, and by what they say about themselves.

Words find a part somebody already half knows. Choosing one that is not known
yet goes the other way: a category, and then what is wanted of it -- 10k, 1%,
0603. Within a category this says, with the parts that match, what they can
still be narrowed by: each parameter, the values it takes among the parts
left, and how many parts each value would leave.

Those counts are of the list as every *other* parameter has narrowed it.
Choosing 0603 does not make 0402 vanish from the packages: it is what
choosing it instead would give.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.parameters import Choice, Condition, Op, Parameter, by_parameter, satisfied
from app.domain.parts import Part
from app.domain.quantities import in_order, magnitude
from app.errors import Invalid
from app.library.snapshot import FOOTPRINT, MANUFACTURER, Snapshot

# More than anyone narrows a list by; a guard, since each is read from the
# address of a request.
MAX_CONDITIONS = 30
MAX_CONDITION_LENGTH = 300

# KiCad's own field, and for a category without a template the one that says
# most. It leads the fields a template does not name.
_VALUE = "Value"


class UnreadableSearch(Invalid):
    """Conditions that say nothing, each named in a sentence."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Found:
    parts: list[Part]  # every match, by IPN
    # What they can be narrowed by next. Empty outside a category.
    parameters: tuple[Parameter, ...] = ()


def conditions_of(where: Sequence[str]) -> tuple[Condition, ...]:
    """The conditions as they were written, read. Raises `UnreadableSearch`,
    naming each one that says nothing."""
    if len(where) > MAX_CONDITIONS:
        raise UnreadableSearch([f"At most {MAX_CONDITIONS} conditions can be put to a search."])
    found, problems = [], []
    for text in where:
        if len(text) > MAX_CONDITION_LENGTH:
            problems.append("A condition is too long to be one.")
            continue
        try:
            found.append(Condition.parse(text))
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        raise UnreadableSearch(problems)
    return tuple(found)


def find(
    snapshot: Snapshot,
    query: str = "",
    *,
    status: str | None = None,
    category: str | None = None,
    where: Sequence[str] = (),
) -> Found:
    """The parts that match the words, the status, the category and every
    condition; and, within a category, what they can be narrowed by."""
    asked = by_parameter(conditions_of(where))
    listed = snapshot.search(query, status=status, category=category)

    def meets(part: Part, *, apart_from: str | None = None) -> bool:
        said = snapshot.parameter_values(part)
        return all(
            satisfied(said.get(name, ()), conditions)
            for name, conditions in asked.items()
            if name != apart_from
        )

    matched = [part for part in listed if meets(part)] if asked else listed
    if category is None:
        return Found(matched)

    # Which parameters there are, and whether one is a quantity, is said by
    # the category as a whole and not by the list as it is narrowed: the
    # controls should not come and go under the hand that is using them.
    everything = snapshot.search(category=category)
    all_values: dict[str, set[str]] = {}
    for part in everything:
        for name, values in snapshot.parameter_values(part).items():
            all_values.setdefault(name, set()).update(values)

    parameters = []
    for name in _in_order(snapshot, category, set(all_values) | set(asked)):
        chosen = {c.value for c in asked.get(name, ()) if c.op is Op.IS}
        known = all_values.get(name, set())
        # With one value, or none, there is nothing to narrow by -- unless it
        # is being narrowed by already, which has to stay in sight.
        if len(known) < 2 and name not in asked:
            continue

        counts: Counter[str] = Counter()
        for part in listed:
            if meets(part, apart_from=name):
                counts.update(set(snapshot.parameter_values(part).get(name, ())))
        parameters.append(
            Parameter(
                name=name,
                numeric=bool(known) and all(magnitude(value) is not None for value in known),
                choices=tuple(
                    Choice(value, counts[value]) for value in in_order(sorted(set(counts) | chosen))
                ),
            )
        )
    return Found(matched, tuple(parameters))


def _in_order(snapshot: Snapshot, category: str, names: set[str]) -> list[str]:
    """The parameters as they are offered: the template's fields as the
    template has them, then Value, then the other fields by name, and last
    the two that are not fields."""
    template = snapshot.template(category)
    first = [field.name for field in template.fields] if template else []
    first.append(_VALUE)
    last = [FOOTPRINT, MANUFACTURER]
    rest = sorted(names - set(first) - set(last), key=str.casefold)
    return [name for name in (*dict.fromkeys(first), *rest, *last) if name in names]

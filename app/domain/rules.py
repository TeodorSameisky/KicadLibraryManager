"""What is asked of a part, said once.

Two things are held to these rules. A part already in the library, where
falling short is an issue to list: a part that predates a rule is still a
part, and still places. And a part someone is asking for, where it is a reason
to refuse. The two are worded differently and weighed differently, by whoever
applies the rules -- but what counts as falling short is decided here, for
both, so the form cannot let through what the issues page would then report.

A finding says which rule and, where the rule has one to give, the reason.
Putting that into words is left to the caller, who knows who is reading.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.domain.refs import LibraryRef
from app.domain.templates import Template


class Rule(StrEnum):
    """What a part can fall short of.

    The values are the slugs the issues page groups by, so they are not to be
    changed lightly.
    """

    NO_DESCRIPTION = "no-description"
    NO_SYMBOL = "no-symbol"
    UNQUALIFIED_SYMBOL = "unqualified-symbol"  # not <library>:<name>
    MISSING_SYMBOL = "missing-symbol"  # no source provides it
    UNPLACEABLE = "unplaceable"  # its symbol does not lead to a body
    UNQUALIFIED_FOOTPRINT = "unqualified-footprint"
    MISSING_FOOTPRINT = "missing-footprint"
    TEMPLATE = "template"  # its fields fall short of its category's template
    MULTIPLE_PREFERRED = "multiple-preferred"


@dataclass(frozen=True)
class Finding:
    rule: Rule
    detail: str = ""  # the reason, for a rule that has one to give


class Assets(Protocol):
    """What the rules need to know about the drawings a part names.

    The library's catalog is one of these. Stated here as the two questions
    asked of it, so that the rules do not depend on how assets are indexed.
    """

    def symbol_ancestry(self, ref: LibraryRef) -> object:
        """A symbol and what it extends. LookupError if no source has it,
        ValueError if its chain does not reach a body."""
        ...

    def footprint(self, ref: LibraryRef) -> object | None: ...


def asset_findings(symbol: str, footprint: str, assets: Assets) -> list[Finding]:
    """Whether the symbol and footprint a part names can be found and used.

    Apart from the rest because it needs every source at once: the assets may
    be in another repository than the part.
    """
    found: list[Finding] = []

    if symbol:
        ref = LibraryRef.parse(symbol)
        if ref is None:
            found.append(Finding(Rule.UNQUALIFIED_SYMBOL))
        else:
            try:
                assets.symbol_ancestry(ref)
            except LookupError:
                found.append(Finding(Rule.MISSING_SYMBOL))
            except ValueError as exc:
                found.append(Finding(Rule.UNPLACEABLE, str(exc)))

    # A part need not name a footprint: the symbol may carry its own.
    if footprint:
        ref = LibraryRef.parse(footprint)
        if ref is None:
            found.append(Finding(Rule.UNQUALIFIED_FOOTPRINT))
        elif assets.footprint(ref) is None:
            found.append(Finding(Rule.MISSING_FOOTPRINT))

    return found


def part_findings(
    *,
    description: str,
    symbol: str,
    footprint: str = "",
    fields: Mapping[str, str],
    preferred: Iterable[bool] = (),
    template: Template | None = None,
    assets: Assets | None = None,
) -> list[Finding]:
    """Everything a part falls short of, not just the first thing.

    `preferred` is whether each of its manufacturer parts is marked as the
    preferred one. Without `assets` the symbol and footprint are not looked
    up, for a caller that does that separately with `asset_findings`.
    """
    found: list[Finding] = []

    if not description.strip():
        found.append(Finding(Rule.NO_DESCRIPTION))

    if not symbol:
        found.append(Finding(Rule.NO_SYMBOL))
    if assets is not None:
        found.extend(asset_findings(symbol, footprint, assets))

    # What every part in this category has to say about itself.
    if template is not None:
        found.extend(Finding(Rule.TEMPLATE, problem) for problem in template.problems(fields))

    if sum(bool(marked) for marked in preferred) > 1:
        found.append(Finding(Rule.MULTIPLE_PREFERRED))

    return found

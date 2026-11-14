"""Reading a BOM exported from KiCad.

KiCad's schematic editor writes a BOM as delimited text, and nearly
everything about it is the user's to change: which columns, what they are
headed, the delimiter, whether parts are grouped onto one line, whether runs
of references are written as ranges. So nothing is assumed about the layout.
Columns are found by their headings, and each row is reduced to what matters
here: which part, which reference designators, how many.

The part is identified by its IPN. Symbols placed from this library carry it
in an `IPN` field, and their `Datasheet` field is the address of the part's
page, which ends in the IPN -- so a BOM exported with KiCad's default columns,
which include Datasheet and not IPN, still identifies every part.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

# What a column may be headed, lower-cased. The first heading in the file
# that matches wins.
_HEADINGS = {
    "references": ("reference", "references", "reference(s)", "refs", "ref", "designator"),
    "quantity": ("qty", "quantity", "qty.", "count"),
    "ipn": ("ipn", "internal part number"),
    "datasheet": ("datasheet",),
    "value": ("value",),
    "footprint": ("footprint",),
    "dnp": ("dnp", "do not populate", "do not place"),
}

_DELIMITERS = (",", ";", "\t")
_PART_PAGE = re.compile(r"/ipn/([^/?#\s]+)")
_REFERENCE = re.compile(r"^(?P<prefix>\D*?)(?P<number>\d+)(?P<rest>.*)$")
_RANGE = re.compile(r"^(?P<prefix>\D+)(?P<first>\d+)-(?P=prefix)?(?P<last>\d+)$")
_TRUTHY = {"dnp", "yes", "true", "1", "x", "y"}

# A range wider than this is a typo or a different notation, not a run of parts.
_MAX_RANGE = 1000


class BomError(ValueError):
    """The text cannot be read as a BOM. The message says what to change."""


@dataclass(frozen=True)
class BomLine:
    """One part on a BOM, with everywhere it is fitted."""

    references: tuple[str, ...]
    quantity: int
    ipn: str | None = None  # None: not a part from this library
    value: str = ""
    footprint: str = ""
    dnp: bool = False  # "do not populate": on the board, not fitted


@dataclass(frozen=True)
class ParsedBom:
    lines: tuple[BomLine, ...]
    warnings: tuple[str, ...] = ()

    @property
    def component_count(self) -> int:
        """How many components are fitted."""
        return sum(line.quantity for line in self.lines if not line.dnp)


def reference_key(reference: str) -> tuple[str, int, str]:
    """Sort key that puts R2 before R10."""
    match = _REFERENCE.match(reference)
    if match is None:
        return (reference, 0, "")
    return (match.group("prefix"), int(match.group("number")), match.group("rest"))


def compact_references(references: tuple[str, ...] | list[str]) -> str:
    """References as a person would write them: "C1-C4, C7, R1, R2".

    Three or more in a row become a range. Two do not: "R1-R2" is no shorter
    than "R1, R2" and takes a moment longer to read.
    """
    runs: list[list[str]] = []
    previous: tuple[str, int, str] | None = None

    for reference in sorted(references, key=reference_key):
        key = reference_key(reference)
        # Only a plain prefix-and-number can be part of a run: "R1", not "R1A".
        plain = bool(_REFERENCE.match(reference)) and not key[2]
        if plain and previous is not None and key[:2] == (previous[0], previous[1] + 1):
            runs[-1].append(reference)
        else:
            runs.append([reference])
        previous = key if plain else None

    return ", ".join(f"{run[0]}-{run[-1]}" if len(run) >= 3 else ", ".join(run) for run in runs)


def _references(cell: str) -> list[str]:
    """Split a cell of references, opening out ranges like R1-R4."""
    found: list[str] = []
    for token in re.split(r"[,;\s]+", cell.strip()):
        if not token:
            continue
        span = _RANGE.match(token)
        if span is not None:
            first, last = int(span.group("first")), int(span.group("last"))
            if first < last <= first + _MAX_RANGE:
                found.extend(f"{span.group('prefix')}{n}" for n in range(first, last + 1))
                continue
        found.append(token)
    return found


def _delimiter(header: str) -> str:
    """Whichever candidate the heading row uses most, outside quotes."""
    unquoted = re.sub(r'"[^"]*"', "", header)
    return max(_DELIMITERS, key=unquoted.count)


def _columns(header: list[str]) -> dict[str, int]:
    """Where each thing we read is, by the heading over it."""
    headings = [cell.strip().lower() for cell in header]
    found: dict[str, int] = {}
    for name, accepted in _HEADINGS.items():
        for index, heading in enumerate(headings):
            if heading in accepted:
                found[name] = index
                break
    return found


def _ipn(row: dict[str, str]) -> str | None:
    if row.get("ipn"):
        return row["ipn"]
    page = _PART_PAGE.search(row.get("datasheet", ""))
    return page.group(1) if page else None


def parse_bom(text: str) -> ParsedBom:
    """Read a BOM into one line per part.

    Rows for the same part are merged, so it does not matter whether KiCad
    grouped them: a BOM exported one reference per row comes out the same as
    one grouped by value.
    """
    text = text.lstrip("﻿")
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    if not first_line:
        raise BomError("The file is empty.")

    rows = [
        row
        for row in csv.reader(io.StringIO(text), delimiter=_delimiter(first_line))
        if any(cell.strip() for cell in row)
    ]
    columns = _columns(rows[0])

    if "references" not in columns:
        raise BomError(
            'The file has no "Reference" column, so it does not look like a BOM '
            "exported from KiCad's schematic editor."
        )
    if "ipn" not in columns and "datasheet" not in columns:
        raise BomError(
            'The file has neither an "IPN" nor a "Datasheet" column, so its parts '
            "cannot be identified. Add the IPN field to the BOM export in KiCad."
        )

    warnings: list[str] = []
    merged: dict[tuple, dict] = {}

    for number, cells in enumerate(rows[1:], start=2):
        row = {
            name: cells[index].strip() if index < len(cells) else ""
            for name, index in columns.items()
        }
        references = _references(row["references"])

        quantity = len(references)
        stated = row.get("quantity", "")
        if stated.isdigit():
            if references and int(stated) != len(references):
                warnings.append(
                    f"Row {number} says {stated} but lists {len(references)} "
                    f"reference{'s' if len(references) != 1 else ''}; the references are counted."
                )
            elif not references:
                quantity = int(stated)
        if quantity == 0:
            continue

        ipn = _ipn(row)
        dnp = row.get("dnp", "").lower() in _TRUTHY
        value = "" if row.get("value") == "~" else row.get("value", "")
        footprint = row.get("footprint", "")

        # A part from the library is one line however KiCad split it up. What
        # is not from the library has only its value and footprint to go by.
        key = (ipn, dnp) if ipn else (None, dnp, value, footprint)
        line = merged.setdefault(
            key,
            {"references": [], "quantity": 0, "ipn": ipn, "value": value,
             "footprint": footprint, "dnp": dnp},
        )  # fmt: skip
        line["references"].extend(references)
        line["quantity"] += quantity

    if not merged:
        raise BomError("The file has a heading row but no parts under it.")

    lines = []
    for line in merged.values():
        references = sorted(dict.fromkeys(line["references"]), key=reference_key)
        if len(references) != len(line["references"]):
            warnings.append(
                f"{line['ipn'] or line['value'] or 'A part'} lists a reference more than once; "
                "each is counted once."
            )
            line["quantity"] = len(references)
        lines.append(BomLine(**{**line, "references": tuple(references)}))

    # In the order someone would walk the board: C1, C2, R1, R2, U1.
    lines.sort(
        key=lambda line: reference_key(line.references[0]) if line.references else ("~", 0, "")
    )
    return ParsedBom(lines=tuple(lines), warnings=tuple(warnings))

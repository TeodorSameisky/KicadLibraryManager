"""A stored BOM, set against the library.

The BOM says which parts and how many. The library says what each part is,
who makes it, and whether it should still be designed in. Put together at the
moment of asking -- not when the BOM was uploaded -- so that a part made
obsolete last week shows up on a BOM uploaded last year.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from functools import cached_property

from app.boms.parse import BomLine
from app.boms.store import Bom
from app.library.parts import Mpn, Part
from app.library.snapshot import Snapshot

BAD = "bad"
WARN = "warn"


def _cell(value: object) -> object:
    """Keep a spreadsheet from treating text as a formula.

    The export is opened in Excel, which runs a cell that starts like a
    formula -- and these cells hold text out of repository files and uploads.
    A leading apostrophe makes it text; a value like "-40" is left alone.
    """
    text = str(value)
    looks_like_formula = text[:1] in ("=", "+", "@", "\t", "\r") or (
        text[:1] == "-" and not text[1:2].isdigit()
    )
    return f"'{text}" if looks_like_formula else value


@dataclass(frozen=True)
class Remark:
    """Something about a line that someone should look at."""

    level: str  # BAD: needs fixing. WARN: worth knowing.
    text: str


@dataclass(frozen=True)
class ReportLine:
    line: BomLine
    part: Part | None  # None: not in the library, or not a library part at all
    source: Mpn | None  # the preferred manufacturer part, if the library has it
    alternates: tuple[str, ...]  # the part's other approved manufacturer parts
    remarks: tuple[Remark, ...]

    @property
    def description(self) -> str:
        return self.part.description if self.part else ""

    @property
    def preferred_mpn(self) -> str:
        return (self.part.preferred_mpn if self.part else None) or ""

    @property
    def level(self) -> str | None:
        """The most serious thing said about this line."""
        levels = {remark.level for remark in self.remarks}
        return BAD if BAD in levels else WARN if WARN in levels else None


def _remarks(line: BomLine, part: Part | None, source: Mpn | None) -> list[Remark]:
    if line.ipn is None:
        return [Remark(WARN, "no part number")]
    if part is None:
        return [Remark(BAD, "not in the library")]

    found: list[Remark] = []
    if part.status == "obsolete":
        found.append(Remark(BAD, "obsolete part"))
    elif part.status != "approved":
        found.append(Remark(WARN, f"{part.status} part"))

    if not part.mpns:
        found.append(Remark(WARN, "no source"))
    elif source is None:
        found.append(Remark(WARN, "source not in the library"))
    elif source.lifecycle == "obsolete":
        found.append(Remark(BAD, "source obsolete"))
    elif source.lifecycle == "nrnd":
        found.append(Remark(WARN, "source not for new designs"))
    return found


@dataclass(frozen=True)
class BomReport:
    bom: Bom
    lines: tuple[ReportLine, ...]

    @cached_property
    def part_count(self) -> int:
        """How many different library parts the BOM calls for."""
        return len({row.line.ipn for row in self.lines if row.part is not None})

    @cached_property
    def problems(self) -> int:
        return sum(row.level == BAD for row in self.lines)

    @cached_property
    def cautions(self) -> int:
        return sum(row.level == WARN for row in self.lines)

    def csv(self) -> str:
        """The BOM with the library's columns added, for purchasing."""
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(
            ["Item", "References", "Qty", "DNP", "IPN", "Description", "Value", "Footprint",
             "Status", "MPN", "Manufacturer", "Lifecycle", "Alternates", "Datasheet", "Remarks"]
        )  # fmt: skip
        for item, row in enumerate(self.lines, start=1):
            line, part, source = row.line, row.part, row.source
            writer.writerow(
                _cell(value)
                for value in [
                    item,
                    ", ".join(line.references),
                    line.quantity,
                    "DNP" if line.dnp else "",
                    line.ipn or "",
                    row.description,
                    line.value,
                    line.footprint,
                    part.status if part else "",
                    row.preferred_mpn,
                    source.manufacturer if source else "",
                    source.lifecycle if source else "",
                    ", ".join(row.alternates),
                    (source.datasheet_url or "") if source else "",
                    "; ".join(remark.text for remark in row.remarks),
                ]
            )
        return out.getvalue()


def build_report(bom: Bom, snapshot: Snapshot) -> BomReport:
    lines = []
    for line in bom.lines:
        part = snapshot.part(line.ipn) if line.ipn else None
        source = snapshot.mpn(part.preferred_mpn) if part else None
        lines.append(
            ReportLine(
                line=line,
                part=part,
                source=source,
                alternates=tuple(
                    ref.mpn for ref in (part.mpns if part else ()) if ref.mpn != part.preferred_mpn
                ),
                remarks=tuple(_remarks(line, part, source)),
            )
        )
    return BomReport(bom=bom, lines=tuple(lines))

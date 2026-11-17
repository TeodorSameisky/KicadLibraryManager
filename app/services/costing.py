"""What a BOM costs to build, from what the suppliers say.

A BOM says which parts, and how many to a board. The library says which
manufacturer parts may be bought for each. The suppliers say what those cost,
how many they have, and how few they sell. Put together, for a number of
boards: each line bought where it costs least for the quantity needed, and
what the whole comes to.

Two things are kept apart, because one of them costs calls and the other
does not:

    cost_bom      works the cost out from what the suppliers have already
                  said, as it is kept. It asks nobody, so a page can show it
                  as often as it is looked at.
    ask_for_bom   asks the suppliers about the BOM's manufacturer parts that
                  nothing is kept for, or only an old answer -- for as long as
                  it is given, the preferred sources first. A BOM of three
                  hundred lines is more calls than a supplier allows in a
                  minute, so it is asked about a piece at a time, each piece
                  kept, and the cost worked out again after each.
"""

from __future__ import annotations

import csv
import io
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property

from app.boms.parse import BomLine
from app.boms.report import safe_cell
from app.boms.store import Bom
from app.domain.costing import SHORT, Purchase, choose, purchase
from app.domain.parts import Part
from app.domain.supply import Query, stands_worse
from app.library.snapshot import Snapshot
from app.services.supply import Answer, State, Supply

# More boards than anyone builds in one go: a guard on what is typed.
MAX_BOARDS = 1_000_000

# How long one request may spend asking suppliers before it answers with
# what it has. The page asks again for the rest.
ASK_SECONDS = 8.0

# Why a line has no price, in the words the page and the export use.
NOT_FITTED = "not fitted"
NOT_A_LIBRARY_PART = "not a library part"
NOT_IN_LIBRARY = "not in the library"
NO_SOURCE = "no manufacturer part"
NO_SUPPLIER = "no supplier is set up"
NOT_ASKED = "not asked yet"
NO_PRICE = "no price given"
NOT_LISTED = "no supplier lists it"


@dataclass(frozen=True)
class Source:
    """A manufacturer part a line may be bought as."""

    mpn: str
    manufacturer: str  # as the library has it; "" if it has no file for it
    preferred: bool
    lifecycle: str | None  # as the library has it

    @property
    def query(self) -> Query:
        return Query(self.mpn, self.manufacturer)


@dataclass(frozen=True)
class LineCost:
    item: int  # the line's number, as on the BOM's page
    line: BomLine
    part: Part | None
    needed: int  # how many the boards take; 0 for a line that is not fitted
    bought: Purchase | None  # where it is bought, if it can be
    note: str  # why it has no price; "" where it has one
    # Whether the supplier it is bought from says the part stands worse than
    # the library does: obsolete, or not for new designs.
    warns: bool = False

    @property
    def fitted(self) -> bool:
        return not self.line.dnp


@dataclass(frozen=True)
class Costing:
    bom: Bom
    boards: int
    lines: tuple[LineCost, ...]
    # What the costing is totalled in: the currency most prices are in.
    currency: str
    # How many answers the BOM's manufacturer parts need -- one from each
    # supplier for each -- and how many of those are not here, or are old.
    questions: int
    unasked: int
    suppliers: bool  # whether there is any supplier to ask
    # What each supplier is called, by its id: an offer says which by id.
    names: Mapping[str, str] = field(default_factory=dict)

    def name_of(self, supplier: str) -> str:
        return self.names.get(supplier, supplier)

    @cached_property
    def fitted(self) -> tuple[LineCost, ...]:
        return tuple(line for line in self.lines if line.fitted)

    @cached_property
    def priced(self) -> int:
        return sum(line.bought is not None for line in self.fitted)

    @cached_property
    def totals(self) -> tuple[tuple[str, float], ...]:
        """What it all comes to, in each currency there are prices in: the
        costing's own first. Two currencies are two totals, not one."""
        summed: dict[str, float] = defaultdict(float)
        for line in self.fitted:
            if line.bought is not None:
                summed[line.bought.currency] += line.bought.cost
        order = sorted(summed, key=lambda currency: (currency != self.currency, currency))
        return tuple((currency, summed[currency]) for currency in order)

    @property
    def total(self) -> float:
        """In the costing's own currency."""
        return dict(self.totals).get(self.currency, 0.0)

    @property
    def per_board(self) -> float:
        return self.total / self.boards

    @property
    def complete(self) -> bool:
        """Whether every fitted line has a price."""
        return self.priced == len(self.fitted)

    def csv(self) -> str:
        """The BOM, line by line, with where each is bought and what it costs."""
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(
            ["Item", "References", "Qty per board", "Needed", "IPN", "Description",
             "MPN", "Manufacturer", "Supplier", "Order code", "Buy", "Unit price",
             "Currency", "Line cost", "In stock", "Lead weeks", "Supplier status",
             "Price asked", "Note"]
        )  # fmt: skip
        for row in self.lines:
            bought = row.bought
            offer = bought.offer if bought else None
            notes = [row.note] if row.note else []
            if bought is not None:
                if bought.surplus:
                    notes.append(f"{bought.surplus} more than needed: the smallest order")
                if bought.stock == SHORT:
                    notes.append("short of stock")
                if bought.old:
                    notes.append("price from an answer that no longer holds")
                if row.warns:
                    notes.append("the supplier has it as worse than the library does")
            writer.writerow(
                safe_cell(value)
                for value in [
                    row.item,
                    ", ".join(row.line.references),
                    row.line.quantity,
                    row.needed if row.fitted else "",
                    row.line.ipn or "",
                    row.part.description if row.part else "",
                    bought.mpn if bought else "",
                    offer.manufacturer if offer else "",
                    self.name_of(offer.supplier) if offer else "",
                    offer.sku if offer else "",
                    bought.quantity if bought else "",
                    f"{bought.unit_price:.6g}" if bought else "",
                    bought.currency if bought else "",
                    f"{bought.cost:.2f}" if bought else "",
                    "" if offer is None or offer.stock is None else offer.stock,
                    "" if offer is None or offer.lead_weeks is None else offer.lead_weeks,
                    offer.status if offer else "",
                    time.strftime("%Y-%m-%d", time.gmtime(bought.asked_at))
                    if bought and bought.asked_at
                    else "",
                    "; ".join(notes),
                ]
            )
        return out.getvalue()


def _sources(part: Part, snapshot: Snapshot) -> tuple[Source, ...]:
    found = []
    preferred = part.preferred_mpn
    for ref in part.mpns:
        mpn = snapshot.mpn(ref.mpn)
        found.append(
            Source(
                mpn=ref.mpn,
                manufacturer=mpn.manufacturer if mpn else "",
                preferred=ref.mpn == preferred,
                lifecycle=mpn.lifecycle if mpn else None,
            )
        )
    return tuple(found)


def _queries(bom: Bom, snapshot: Snapshot) -> list[Query]:
    """What the suppliers are asked about for a BOM: each manufacturer part
    its fitted lines may be bought as, once, the preferred sources first --
    those are what a costing needs before anything else."""
    preferred: dict[Query, None] = {}
    others: dict[Query, None] = {}
    for line in bom.lines:
        part = snapshot.part(line.ipn) if line.ipn and not line.dnp else None
        for source in _sources(part, snapshot) if part else ():
            (preferred if source.preferred else others)[source.query] = None
    return [*preferred, *(query for query in others if query not in preferred)]


def _old_or_missing(answer: Answer) -> bool:
    return answer.state is State.NOT_ASKED or answer.stale


def cost_bom(bom: Bom, snapshot: Snapshot, supply: Supply, boards: int = 1) -> Costing:
    """What building `boards` boards from a BOM costs, from what is kept.

    Asks nobody. A line whose manufacturer parts have not been asked about
    yet is shown as that, and `Costing.unasked` says how many answers are
    still to be had.
    """
    boards = max(1, min(boards, MAX_BOARDS))
    manufacturers = snapshot.parts.manufacturers
    kept: dict[Query, tuple[Answer, ...]] = {
        query: supply.kept(query, manufacturers) for query in _queries(bom, snapshot)
    }

    def offered(line: BomLine, part: Part) -> list[Purchase]:
        options = []
        for source in _sources(part, snapshot):
            for answer in kept.get(source.query, ()):
                bought = (
                    purchase(
                        answer.offer,
                        line.quantity * boards,
                        mpn=source.mpn,
                        preferred=source.preferred,
                        asked_at=answer.asked_at,
                        old=answer.stale,
                    )
                    if answer.offer is not None
                    else None
                )
                if bought is not None:
                    options.append(bought)
        return options

    # The currency most prices are in, over the whole BOM: what it is
    # totalled in, and what a line is bought in where it can be.
    counted = Counter(
        option.currency
        for line in bom.lines
        if not line.dnp and line.ipn and (part := snapshot.part(line.ipn)) is not None
        for option in offered(line, part)
        if option.currency
    )
    currency = min(counted, key=lambda name: (-counted[name], name), default="")

    def why_not(line: BomLine, part: Part | None) -> str:
        """Why a line has no price, if it cannot have one whatever is asked."""
        if line.dnp:
            return NOT_FITTED
        if line.ipn is None:
            return NOT_A_LIBRARY_PART
        if part is None:
            return NOT_IN_LIBRARY
        if not part.mpns:
            return NO_SOURCE
        return "" if supply.enabled else NO_SUPPLIER

    def unheard(part: Part) -> str:
        """Why a line that could have a price has none."""
        answers = [answer for source in _sources(part, snapshot) for answer in kept[source.query]]
        if any(answer.offer is not None for answer in answers):
            return NO_PRICE
        if any(answer.state is State.NOT_ASKED for answer in answers):
            return NOT_ASKED
        return NOT_LISTED

    lines = []
    for item, line in enumerate(bom.lines, start=1):
        part = snapshot.part(line.ipn) if line.ipn else None
        needed = 0 if line.dnp else line.quantity * boards
        note = why_not(line, part)
        if note or part is None:
            lines.append(LineCost(item, line, part, needed, None, note))
            continue
        bought = choose(offered(line, part), currency)
        if bought is None:
            lines.append(LineCost(item, line, part, needed, None, unheard(part)))
            continue
        library = next((s.lifecycle for s in _sources(part, snapshot) if s.mpn == bought.mpn), None)
        lines.append(
            LineCost(
                item,
                line,
                part,
                needed,
                bought,
                "",
                warns=stands_worse(bought.offer.lifecycle, library),
            )
        )

    answers = [answer for found in kept.values() for answer in found]
    return Costing(
        bom=bom,
        boards=boards,
        lines=tuple(lines),
        currency=currency,
        questions=len(answers),
        unasked=sum(_old_or_missing(answer) for answer in answers),
        suppliers=supply.enabled,
        names={answer.supplier: answer.name for answer in answers},
    )


def ask_for_bom(
    bom: Bom,
    snapshot: Snapshot,
    supply: Supply,
    *,
    seconds: float = ASK_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Ask the suppliers about a BOM's manufacturer parts, for as long as
    `seconds` allows: those nothing is kept for, or only an old answer.

    What a supplier will not be asked now -- its calls for the day or the
    minute are used up, it just failed -- `Supply` declines without a call,
    so asking again soon costs nothing. Returns how many manufacturer parts
    were asked about.
    """
    if not supply.enabled:
        return 0
    manufacturers = snapshot.parts.manufacturers
    deadline = clock() + seconds
    asked = 0
    for query in _queries(bom, snapshot):
        if clock() >= deadline:
            break
        if any(_old_or_missing(answer) for answer in supply.kept(query, manufacturers)):
            supply.ask(query, manufacturers)
            asked += 1
    return asked


def boards_of(values: Iterable[str]) -> int:
    """A number of boards, as typed: whole, at least one."""
    for value in values:
        try:
            return max(1, min(int(value.strip()), MAX_BOARDS))
        except ValueError:
            continue
    return 1

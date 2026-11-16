"""What a supplier says about a manufacturer part: whether it can be bought,
what it costs, and whether it is still being made.

The library says which manufacturer parts may be bought for a part. Whether
one *can* be, today, is not something the library knows: that is a supplier's
to say, and it changes daily. So it is asked, kept for a while, and shown
beside what the library says -- never written into it.

Every supplier answers in its own shape. These are the few things they all
say, in one shape, so that the rest of the app can show two suppliers side by
side without knowing either.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    """What a supplier is asked about: a manufacturer part, by its number and
    who makes it. The maker is what tells two parts with one number apart."""

    mpn: str
    manufacturer: str = ""


@dataclass(frozen=True)
class PriceBreak:
    quantity: int  # from this many
    unit_price: float  # each, in the offer's currency


@dataclass(frozen=True)
class Offer:
    """One supplier's entry for one manufacturer part."""

    supplier: str  # which supplier: its id, as in "digikey"
    mpn: str  # the manufacturer's part number, as the supplier writes it
    manufacturer: str  # and the manufacturer, as the supplier calls it
    sku: str = ""  # the supplier's own number for it
    description: str = ""
    stock: int | None = None  # what it has now; None if it does not say
    # One of `app.domain.parts.Lifecycle`, worked out from what the supplier
    # says, so that it can be set against what the library says...
    lifecycle: str = "unknown"
    # ...and what the supplier said, in its own words: "Last Time Buy".
    status: str = ""
    # For the packaging with the smallest order: what someone buying a few
    # to build a prototype pays.
    prices: tuple[PriceBreak, ...] = ()
    currency: str = ""
    packaging: str = ""
    minimum: int | None = None  # the fewest that can be ordered
    lead_weeks: int | None = None  # the manufacturer's lead time
    url: str | None = None  # its page at the supplier
    datasheet: str | None = None

    def price_at(self, quantity: int) -> float | None:
        """What one costs when buying `quantity`: the last break reached.

        None if the offer has no prices, or the quantity is below its first
        break -- fewer than can be bought.
        """
        reached = [step for step in self.prices if step.quantity <= quantity]
        return max(reached, key=lambda step: step.quantity).unit_price if reached else None


# How a part stands, from fine to gone. "unknown" is not on the scale: it
# says nothing either way.
_STANDING = {"active": 0, "nrnd": 1, "obsolete": 2}


def stands_worse(said: str, recorded: str | None) -> bool:
    """Whether a supplier says a part stands worse than the library records.

    That is the reason to look at it: a part the library has as active and a
    supplier has as obsolete. A supplier that says it stands better, or says
    nothing, is no cause for alarm. Where the library records nothing, "not
    for new designs" or worse is already news.
    """
    theirs = _STANDING.get(said)
    if theirs is None:
        return False
    return theirs > _STANDING.get(recorded or "", 0)

"""What buying a line of a BOM from one supplier comes to.

A supplier's prices are per piece and depend on how many are bought. A BOM
says how many of each part one board takes; building a batch of boards takes
that many times more. And what can be bought is not always what is needed:
a supplier that sells from ten does not sell three. So a purchase is worked
out from what is needed and what the offer allows, and that -- not the price
of one piece -- is what offers are compared by.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.supply import Offer

# Whether a supplier has enough to fill an order: in order of preference.
ENOUGH = "enough"
UNKNOWN = "unknown"  # it does not say how many it has
SHORT = "short"

_STOCK_ORDER = {ENOUGH: 0, UNKNOWN: 1, SHORT: 2}


@dataclass(frozen=True)
class Purchase:
    """Buying what a BOM line needs from one supplier, as one manufacturer part."""

    offer: Offer
    mpn: str  # the manufacturer part, as the library has it
    preferred: bool  # whether it is the part's preferred source
    needed: int  # how many the boards take
    quantity: int  # how many are bought: at least what is needed, at least what can be
    unit_price: float  # each, at that quantity, in the offer's currency
    asked_at: float | None = None  # when the supplier said so; epoch seconds
    old: bool = False  # whether that answer no longer holds, and is shown for want of one

    @property
    def cost(self) -> float:
        return self.quantity * self.unit_price

    @property
    def currency(self) -> str:
        return self.offer.currency

    @property
    def surplus(self) -> int:
        """How many more are bought than are needed: the smallest order."""
        return self.quantity - self.needed

    @property
    def stock(self) -> str:
        if self.offer.stock is None:
            return UNKNOWN
        return ENOUGH if self.offer.stock >= self.quantity else SHORT


def purchase(
    offer: Offer,
    needed: int,
    *,
    mpn: str,
    preferred: bool = False,
    asked_at: float | None = None,
    old: bool = False,
) -> Purchase | None:
    """Buying `needed` from an offer, or None if it gives no price.

    What is bought is what is needed, or the fewest the offer sells if that is
    more: its minimum order, or the first of its price breaks. The price is
    the one for the quantity bought.
    """
    if not offer.prices:
        return None
    first = min(step.quantity for step in offer.prices)
    quantity = max(needed, offer.minimum or 1, first)
    unit_price = offer.price_at(quantity)
    if unit_price is None:  # cannot happen once the first break is reached
        return None
    return Purchase(
        offer=offer,
        mpn=mpn,
        preferred=preferred,
        needed=needed,
        quantity=quantity,
        unit_price=unit_price,
        asked_at=asked_at,
        old=old,
    )


def choose(purchases: Iterable[Purchase], currency: str) -> Purchase | None:
    """The purchase to make, of those on offer.

    From a supplier with enough in stock, before one that does not say,
    before one that is short: a cheap price for what cannot be delivered is
    not a price. Then in `currency`, the one the costing is totalled in --
    prices in two currencies cannot be compared without a rate, and none is
    guessed at. Then the cheapest for the quantity bought; and, between two
    that cost the same, the part's preferred source.
    """
    return min(
        purchases,
        key=lambda bought: (
            _STOCK_ORDER[bought.stock],
            bought.currency != currency,
            bought.cost,
            not bought.preferred,
            bought.offer.supplier,
        ),
        default=None,
    )

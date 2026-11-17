"""What suppliers say about a part's manufacturer parts, over the API."""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.parts import Part
from app.domain.supply import stands_worse
from app.services.supply import Answer, SourceSupply


class PriceOut(BaseModel):
    quantity: int  # from this many
    unit_price: float  # each, in the answer's currency


class AnswerOut(BaseModel):
    """What one supplier says about one manufacturer part, or why it says
    nothing."""

    supplier: str  # its id: "digikey"
    name: str  # what it is called
    # "found", "not-found" (asked, and it does not list the part),
    # "over-limit" (not asked: the calls it allows are used up for now),
    # "failed", or -- where what is kept is looked at without asking --
    # "not-asked".
    state: str
    asked_at: float | None  # when what is shown was asked; epoch seconds
    # Whether it is an answer that no longer holds, shown because a new one
    # could not be had.
    stale: bool

    # The rest is the offer, and is None or empty unless the state is "found".
    sku: str | None  # the supplier's own number for the part
    manufacturer: str | None  # the manufacturer, as the supplier calls it
    description: str | None
    stock: int | None
    lifecycle: str | None  # in the library's words: active, nrnd, obsolete, unknown
    status: str | None  # in the supplier's own
    # Whether the supplier says the part stands worse than the library does:
    # the reason to look at it.
    warns: bool
    currency: str | None
    packaging: str | None
    minimum: int | None  # the fewest that can be ordered
    lead_weeks: int | None
    prices: list[PriceOut]
    url: str | None  # the part's page at the supplier
    datasheet: str | None

    @classmethod
    def of(cls, answer: Answer, library_lifecycle: str | None) -> AnswerOut:
        offer = answer.offer
        return cls(
            supplier=answer.supplier,
            name=answer.name,
            state=answer.state.value,
            asked_at=answer.asked_at,
            stale=answer.stale,
            sku=offer.sku if offer else None,
            manufacturer=offer.manufacturer if offer else None,
            description=offer.description if offer else None,
            stock=offer.stock if offer else None,
            lifecycle=offer.lifecycle if offer else None,
            status=offer.status if offer else None,
            warns=offer is not None and stands_worse(offer.lifecycle, library_lifecycle),
            currency=offer.currency if offer else None,
            packaging=offer.packaging if offer else None,
            minimum=offer.minimum if offer else None,
            lead_weeks=offer.lead_weeks if offer else None,
            prices=[
                PriceOut(quantity=step.quantity, unit_price=step.unit_price)
                for step in (offer.prices if offer else ())
            ],
            url=offer.url if offer else None,
            datasheet=offer.datasheet if offer else None,
        )


class SourceSupplyOut(BaseModel):
    """A manufacturer part of the library, and what the suppliers say of it."""

    mpn: str
    manufacturer: str
    lifecycle: str | None  # as the library has it; None if it has no file for it
    answers: list[AnswerOut]  # one for each supplier that can be asked


class PartSupply(BaseModel):
    ipn: str
    sources: list[SourceSupplyOut]

    @classmethod
    def of(cls, part: Part, sources: tuple[SourceSupply, ...]) -> PartSupply:
        return cls(
            ipn=part.ipn,
            sources=[
                SourceSupplyOut(
                    mpn=source.mpn,
                    manufacturer=source.manufacturer,
                    lifecycle=source.lifecycle,
                    answers=[AnswerOut.of(answer, source.lifecycle) for answer in source.answers],
                )
                for source in sources
            ],
        )

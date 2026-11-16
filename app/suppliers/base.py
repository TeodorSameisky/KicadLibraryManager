"""What a supplier has to be able to do."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.domain.manufacturers import Manufacturers
from app.domain.supply import Offer, Query
from app.errors import UpstreamFailed


class SupplierError(UpstreamFailed):
    """A supplier could not be asked, or did not answer. The message is for
    whoever runs the app -- the log, and the status page: it may quote what
    the supplier said, and never holds a key."""

    def detail(self) -> str:
        return "The supplier did not answer."


@dataclass(frozen=True)
class Fetched:
    """What a supplier sent back to one question, as it came."""

    status: int  # the HTTP status
    body: str  # the answer, unread
    # What the supplier says is left of the day's calls, if it says. Only
    # shown: the count that decides anything is the app's own.
    remaining: int | None = field(default=None, compare=False)
    # Where it is not an answer to the question: the supplier's reason, in a
    # line, for whoever runs the app. Never holds a key.
    said: str = field(default="", compare=False)

    @property
    def answered(self) -> bool:
        """Whether this is an answer to the question -- that it has the part,
        or that it has not -- and so worth keeping for as long as an answer
        is. Anything else is the supplier being unable to say just now."""
        return self.status == 200 or self.status == 404

    @property
    def refused(self) -> bool:
        """Whether the supplier said there have been too many calls."""
        return self.status == 429


class Supplier(Protocol):
    @property
    def id(self) -> str:
        """A short, stable name: it is in the database, and in settings."""
        ...

    @property
    def name(self) -> str:
        """What it is called, for a page."""
        ...

    @property
    def configured(self) -> bool:
        """Whether this deployment has what it takes to ask it: its keys."""
        ...

    def asks(self, query: Query) -> str:
        """What will be sent for this query, as text.

        Two queries that send the same thing say the same here, and are then
        one question: asked once, answered from what is kept after that.
        Whatever changes the answer -- the part number, the country the
        prices are for -- has to be in it.
        """
        ...

    def fetch(self, query: Query) -> Fetched:
        """Ask the supplier. One call over the network, and blocking.

        Raises `SupplierError` if it could not be reached at all. Whatever it
        answered, even to refuse, is returned and not raised.
        """
        ...

    def read(self, body: str, query: Query, manufacturers: Manufacturers) -> Offer | None:
        """What an answer says about the part asked for, or None if it is not
        among what the supplier found.

        `manufacturers` is the library's list: a supplier has its own name for
        each manufacturer, and the list is what says two names are one.
        """
        ...

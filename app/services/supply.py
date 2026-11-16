"""Asking suppliers about manufacturer parts, without asking twice.

A supplier allows only so many calls a day. So before one is asked, three
things are looked at, the same way for every supplier:

    is the answer already here?    then it is not asked again while it holds
    are today's calls used up?     then it is not asked, and an old answer,
                                   if there is one, is shown as old
    did it just fail?              then it is left alone for a few minutes

Only then is the call made, and counted. What comes back is kept as the
supplier gave it; `Supplier.read` says what it means each time it is shown,
so a better reader can be put to answers already paid for.

Nothing here is written to the library. What a supplier says is shown beside
what the library says, and it is for a person to decide what follows.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.db.database import DatabaseUnavailable
from app.domain.manufacturers import Manufacturers
from app.domain.parts import Mpn, Part
from app.domain.supply import Offer, Query
from app.library.snapshot import Snapshot
from app.suppliers.base import Supplier, SupplierError
from app.suppliers.store import Kept, SupplierStore

log = logging.getLogger(__name__)

# How long a supplier is left alone after it failed to answer a question.
RETRY_AFTER_SECONDS = 600


def _reason(what: str, said: str) -> str:
    """Why a supplier did not answer: what happened, and its own words."""
    return f"{what}: {said}" if said else what


class State(StrEnum):
    FOUND = "found"  # the supplier has the part: there is an offer
    NOT_FOUND = "not-found"  # it was asked, and does not list it
    OVER_LIMIT = "over-limit"  # not asked: today's calls are used up
    FAILED = "failed"  # asked, and it did not answer


@dataclass(frozen=True)
class Answer:
    """What one supplier says about one manufacturer part, or why it says
    nothing."""

    supplier: str
    name: str
    state: State
    offer: Offer | None = None
    asked_at: float | None = None  # when what is shown was asked; epoch seconds
    # Whether it is an answer that no longer holds, shown because a new one
    # could not be had.
    stale: bool = False


@dataclass(frozen=True)
class SourceSupply:
    """A manufacturer part of the library, and what the suppliers say of it."""

    mpn: str
    manufacturer: str
    lifecycle: str | None  # as the library has it; None if it has no file for it
    answers: tuple[Answer, ...]


@dataclass(frozen=True)
class SupplierUsage:
    id: str
    name: str
    configured: bool
    calls_today: int
    limit: int  # how many calls a day it may be sent
    answers: int  # how many answers are kept
    last_asked: float | None
    # The last time it did not answer, and why, unless it has answered since.
    failed_at: float | None = None
    failure: str = ""


class Supply:
    def __init__(
        self,
        suppliers: Sequence[Supplier],
        store: SupplierStore,
        *,
        limits: Mapping[str, int],
        holds_for_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._suppliers = tuple(suppliers)
        self._store = store
        self._limits = dict(limits)
        self._holds_for = holds_for_seconds
        self._clock = clock
        # One question at a time to each supplier: two people opening the
        # same page must not make the same call twice.
        self._locks = {supplier.id: threading.Lock() for supplier in self._suppliers}
        # Until when a question, or a whole supplier, is left alone after a
        # failure. In memory: a restart costs one more attempt and no more.
        self._left_alone: dict[tuple[str, str], float] = {}
        # When each supplier last failed and why, until it answers again:
        # for whoever runs the app, who cannot see it on a part's page.
        self._failures: dict[str, tuple[float, str]] = {}

    @property
    def enabled(self) -> bool:
        """Whether there is any supplier this deployment can ask."""
        return any(supplier.configured for supplier in self._suppliers)

    def limit(self, supplier: Supplier) -> int:
        return self._limits.get(supplier.id, 0)

    # -- asking

    def ask(self, query: Query, manufacturers: Manufacturers) -> tuple[Answer, ...]:
        """What each supplier that can be asked says about a manufacturer part.

        Blocking: it may call out to each of them. Raises `DatabaseUnavailable`
        if nothing can be kept: without a place for the answers and the count,
        every look at a page would be a call.
        """
        return tuple(
            self._ask(supplier, query, manufacturers)
            for supplier in self._suppliers
            if supplier.configured
        )

    def _ask(self, supplier: Supplier, query: Query, manufacturers: Manufacturers) -> Answer:
        asked = supplier.asks(query)
        with self._locks[supplier.id]:
            now = self._clock()
            kept = self._store.get(supplier.id, asked)
            if kept is not None and kept.holds(now):
                return self._answer(supplier, kept, query, manufacturers)

            def fall_back(state: State) -> Answer:
                """The old answer, said to be old, if there is one."""
                if kept is not None:
                    return self._answer(supplier, kept, query, manufacturers, stale=True)
                return Answer(supplier.id, supplier.name, state)

            if self._alone(supplier.id, asked, now):
                return fall_back(State.FAILED)
            if self._store.calls(supplier.id, now) >= self.limit(supplier):
                return fall_back(State.OVER_LIMIT)

            # Counted before it is made: a call that fails halfway was still
            # a call to the supplier.
            self._store.count_call(supplier.id, now)
            try:
                fetched = supplier.fetch(query)
            except SupplierError as exc:
                log.warning("%s could not be asked about %s: %s", supplier.name, query.mpn, exc)
                self._failures[supplier.id] = (now, str(exc))
                self._left_alone[(supplier.id, asked)] = now + RETRY_AFTER_SECONDS
                return fall_back(State.FAILED)

            if fetched.answered:
                self._failures.pop(supplier.id, None)
                kept = self._store.put(
                    supplier.id,
                    asked,
                    fetched.status,
                    fetched.body,
                    now=now,
                    holds_for=self._holds_for,
                )
                return self._answer(supplier, kept, query, manufacturers)

            if fetched.refused:
                # Its count of the day is ahead of this one. Nothing more is
                # sent to it for a while, whatever is asked.
                log.warning(
                    "%s says it has been called too often today: %s", supplier.name, fetched.said
                )
                self._failures[supplier.id] = (
                    now,
                    _reason("It says it has been called too often", fetched.said),
                )
                self._left_alone[(supplier.id, "")] = now + RETRY_AFTER_SECONDS
                return fall_back(State.OVER_LIMIT)

            log.warning(
                "%s answered HTTP %d when asked about %s: %s",
                supplier.name,
                fetched.status,
                query.mpn,
                fetched.said,
            )
            self._failures[supplier.id] = (
                now,
                _reason(f"It answered HTTP {fetched.status}", fetched.said),
            )
            self._left_alone[(supplier.id, asked)] = now + RETRY_AFTER_SECONDS
            return fall_back(State.FAILED)

    def _alone(self, supplier: str, asked: str, now: float) -> bool:
        return any(
            self._left_alone.get(key, 0.0) > now for key in ((supplier, asked), (supplier, ""))
        )

    @staticmethod
    def _answer(
        supplier: Supplier,
        kept: Kept,
        query: Query,
        manufacturers: Manufacturers,
        *,
        stale: bool = False,
    ) -> Answer:
        offer = supplier.read(kept.body, query, manufacturers) if kept.status == 200 else None
        return Answer(
            supplier=supplier.id,
            name=supplier.name,
            state=State.FOUND if offer is not None else State.NOT_FOUND,
            offer=offer,
            asked_at=kept.fetched_at,
            stale=stale,
        )

    def for_part(self, part: Part, snapshot: Snapshot) -> tuple[SourceSupply, ...]:
        """What the suppliers say about each manufacturer part a part lists."""
        manufacturers = snapshot.parts.manufacturers
        found = []
        for ref in part.mpns:
            mpn: Mpn | None = snapshot.mpn(ref.mpn)
            maker = mpn.manufacturer if mpn else ""
            found.append(
                SourceSupply(
                    mpn=ref.mpn,
                    manufacturer=maker,
                    lifecycle=mpn.lifecycle if mpn else None,
                    answers=self.ask(Query(ref.mpn, maker), manufacturers),
                )
            )
        return tuple(found)

    # -- for whoever runs it

    def usage(self) -> tuple[SupplierUsage, ...]:
        """Each supplier, whether it can be asked, and how much it has been."""
        now = self._clock()
        found = []
        for supplier in self._suppliers:
            try:
                used = self._store.usage(supplier.id, now)
                calls, answers, last = used.calls_today, used.answers, used.last_asked
            except DatabaseUnavailable:
                calls, answers, last = 0, 0, None  # said elsewhere on the page
            failed_at, failure = self._failures.get(supplier.id, (None, ""))
            found.append(
                SupplierUsage(
                    id=supplier.id,
                    name=supplier.name,
                    configured=supplier.configured,
                    calls_today=calls,
                    limit=self.limit(supplier),
                    answers=answers,
                    last_asked=last,
                    failed_at=failed_at,
                    failure=failure,
                )
            )
        return tuple(found)

"""What suppliers were asked and what they answered, kept.

Two things, in the database (`app.db`), which is where the tables are made.

The answers: each under a hash of which supplier was asked and what, as the
supplier gave it. Asking costs a call, and a supplier allows only so many a
day, so the same thing is not asked twice while the answer still holds. An
answer that no longer holds is not thrown away at once: when the day's calls
are used up, an old answer is better than none.

And the count: how many calls went to each supplier on each day, which is
what the limit is held against and what the status page shows.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from app.db.database import Database

# Answers are kept this long past the time they stopped holding, to fall back
# on. After that they describe a market that is gone.
KEEP_STALE_SECONDS = 30 * 24 * 3600


def answer_key(supplier: str, asked: str) -> str:
    """Where an answer is kept: a hash of who was asked and what."""
    return hashlib.sha256(f"{supplier}\n{asked}".encode()).hexdigest()


def day_of(moment: float) -> str:
    """The day a call counts towards. In UTC, as suppliers count theirs."""
    return time.strftime("%Y-%m-%d", time.gmtime(moment))


@dataclass(frozen=True)
class Kept:
    """An answer, as it was kept."""

    status: int
    body: str
    fetched_at: float  # epoch seconds
    expires_at: float  # when it stops holding

    def holds(self, now: float) -> bool:
        return now < self.expires_at


@dataclass(frozen=True)
class Usage:
    calls_today: int
    answers: int  # how many answers are kept
    last_asked: float | None  # when it was last asked anything


class SupplierStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    # -- answers

    def get(self, supplier: str, asked: str) -> Kept | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status, body, fetched_at, expires_at FROM supplier_answers WHERE key = ?",
                (answer_key(supplier, asked),),
            ).fetchone()
        return Kept(**{key: row[key] for key in row.keys()}) if row else None  # noqa: SIM118

    def put(
        self, supplier: str, asked: str, status: int, body: str, *, now: float, holds_for: float
    ) -> Kept:
        """Keep an answer, in place of whatever was kept for the same question."""
        kept = Kept(status=status, body=body, fetched_at=now, expires_at=now + holds_for)
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO supplier_answers"
                " (key, supplier, asked, status, body, fetched_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    answer_key(supplier, asked),
                    supplier,
                    asked,
                    status,
                    body,
                    kept.fetched_at,
                    kept.expires_at,
                ),
            )
            # What has been stale for long enough goes now, so the table is
            # as long as what is being looked at and no longer.
            connection.execute(
                "DELETE FROM supplier_answers WHERE expires_at < ?", (now - KEEP_STALE_SECONDS,)
            )
        return kept

    # -- calls

    def calls(self, supplier: str, now: float) -> int:
        """How many calls have gone to a supplier today."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT calls FROM supplier_calls WHERE supplier = ? AND day = ?",
                (supplier, day_of(now)),
            ).fetchone()
        return int(row["calls"]) if row else 0

    def count_call(self, supplier: str, now: float) -> int:
        """Note a call to a supplier. Returns how many that makes today."""
        day = day_of(now)
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO supplier_calls (supplier, day, calls) VALUES (?, ?, 1)"
                " ON CONFLICT (supplier, day) DO UPDATE SET calls = calls + 1",
                (supplier, day),
            )
            row = connection.execute(
                "SELECT calls FROM supplier_calls WHERE supplier = ? AND day = ?", (supplier, day)
            ).fetchone()
        return int(row["calls"])

    def usage(self, supplier: str, now: float) -> Usage:
        with self.database.connect() as connection:
            kept = connection.execute(
                "SELECT COUNT(*) AS answers, MAX(fetched_at) AS last FROM supplier_answers"
                " WHERE supplier = ?",
                (supplier,),
            ).fetchone()
            today = connection.execute(
                "SELECT calls FROM supplier_calls WHERE supplier = ? AND day = ?",
                (supplier, day_of(now)),
            ).fetchone()
        return Usage(
            calls_today=int(today["calls"]) if today else 0,
            answers=int(kept["answers"]),
            last_asked=kept["last"],
        )

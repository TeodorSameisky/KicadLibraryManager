"""Keeps a current snapshot of the library available while the app runs.

Indexing means cloning or fetching every source and reading every file in it.
For a repository carrying 3D models that takes long enough to fail a container
health check, so it never happens during startup: the app starts at once,
reports that it is indexing, and swaps the finished snapshot in when it lands.

After that the library is looked at again whenever something says it may have
changed -- a webhook from the git host, a librarian pressing a button -- and
on a timer for when nothing says so. Looking is cheap: if no source has moved
to a new commit, the snapshot being served is kept as it is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from enum import Enum
from pathlib import Path

from app.library.snapshot import Snapshot, SyncFailure, build_snapshot
from app.library.sources import Checkout, Source, SourceError, sync

log = logging.getLogger(__name__)

Sync = Callable[[Source, Path], Checkout]


class State(str, Enum):
    EMPTY = "empty"  # no sources are configured
    SYNCING = "syncing"  # the first index is still being built
    READY = "ready"
    ERROR = "error"  # indexing failed and there is no earlier index to serve


class LibraryService:
    def __init__(
        self,
        sources: Sequence[Source],
        workdir: Path,
        *,
        sync: Sync = sync,
        refresh_interval_seconds: float = 0,
    ) -> None:
        self._sources = tuple(sources)
        self._workdir = workdir
        self._sync = sync
        self._interval = refresh_interval_seconds
        self._snapshot = Snapshot()
        self._state = State.SYNCING if self._sources else State.EMPTY
        self._error: str | None = None
        self._checked_at: float | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._requested: asyncio.Task[None] | None = None

    @property
    def state(self) -> State:
        return self._state

    @property
    def error(self) -> str | None:
        """Why the last index failed, while an earlier one is still served."""
        return self._error

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    @property
    def checked_at(self) -> float | None:
        """When the sources were last looked at, whether or not they had moved."""
        return self._checked_at

    # -- lifecycle

    def start(self) -> None:
        """Begin the first index in the background, then keep watch."""
        if self._sources and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        tasks = [task for task in (self._task, self._requested) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = self._requested = None

    async def _run(self) -> None:
        await self._refresh_safely("the first library index")
        while self._interval > 0:
            await asyncio.sleep(self._interval)
            await self._refresh_safely("a scheduled library refresh")

    async def _refresh_safely(self, what: str) -> None:
        """Refresh from the background, where there is nobody to raise to."""
        try:
            await self.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:  # whatever went wrong, the app keeps serving
            log.exception("%s failed", what)
            self._error = "indexing failed unexpectedly; the server log has the details"
            if self._snapshot.indexed_at is None:
                self._state = State.ERROR

    def request_refresh(self) -> bool:
        """Ask for a refresh without waiting for it.

        For a webhook, which has ten seconds to be answered and no use for the
        result. Requests arriving while one is already waiting are folded into
        it: a push of five commits is one change to the library, not five.
        Returns whether there is anything to refresh.
        """
        if not self._sources:
            return False
        if self._requested is None or self._requested.done():
            self._requested = asyncio.create_task(self._refresh_safely("a requested refresh"))
        return True

    # -- indexing

    async def refresh(self) -> Snapshot:
        """Sync every source and index the result. Callers queue, not overlap."""
        async with self._lock:
            if not self._sources:
                return self._snapshot

            snapshot = await asyncio.to_thread(self._index, self._snapshot)
            self._checked_at = time.time()

            if snapshot is self._snapshot:
                self._error = None  # every source answered, and none had moved
                return snapshot

            if snapshot.failed:
                # Losing the git host must not empty a catalog people are
                # using: the previous index stays, and the failure is shown.
                self._error = "; ".join(f"{s.id}: {s.error}" for s in snapshot.sources)
                if self._snapshot.indexed_at is None:
                    self._state = State.ERROR
                    self._snapshot = snapshot  # so each source's error is visible
                return self._snapshot

            self._snapshot = snapshot
            self._state = State.READY
            self._error = None
            return snapshot

    def _index(self, current: Snapshot) -> Snapshot:
        """Blocking: runs git and reads every file. Called off the event loop.

        Returns `current` itself when nothing has changed, so that the caller
        can tell without comparing anything.
        """
        started = time.perf_counter()

        outcomes: list[Checkout | SyncFailure] = []
        for source in self._sources:
            try:
                outcomes.append(self._sync(source, self._workdir))
            except SourceError as exc:
                log.warning("source %s failed to sync: %s", source.id, exc)
                outcomes.append(SyncFailure(source, str(exc)))

        # Every source at the commit that is already indexed: there is nothing
        # new to read. A failure anywhere is always worth a fresh look.
        commits = [o.commit if isinstance(o, Checkout) else None for o in outcomes]
        indexed = current.indexed_at is not None
        if indexed and None not in commits and commits == [s.commit for s in current.sources]:
            return current

        snapshot = replace(
            build_snapshot(outcomes),
            indexed_at=time.time(),
            duration=time.perf_counter() - started,
        )
        log.info(
            "indexed %d source(s) in %.1fs: %d parts, %d symbols, %d errors, %d warnings",
            len(snapshot.sources),
            snapshot.duration,
            snapshot.part_count,
            snapshot.catalog.symbol_count,
            len(snapshot.errors),
            len(snapshot.warnings),
        )
        return snapshot

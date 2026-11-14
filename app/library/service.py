"""Keeps a current snapshot of the library available while the app runs.

Indexing means cloning or fetching every source and reading every file in it.
For a repository carrying 3D models that takes long enough to fail a container
health check, so it never happens during startup: the app starts at once,
reports that it is indexing, and swaps the finished snapshot in when it lands.
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
    def __init__(self, sources: Sequence[Source], workdir: Path, *, sync: Sync = sync) -> None:
        self._sources = tuple(sources)
        self._workdir = workdir
        self._sync = sync
        self._snapshot = Snapshot()
        self._state = State.SYNCING if self._sources else State.EMPTY
        self._error: str | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

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

    # -- lifecycle

    def start(self) -> None:
        """Begin the first index in the background."""
        if self._sources and self._task is None:
            self._task = asyncio.create_task(self._first_index())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _first_index(self) -> None:
        try:
            await self.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:  # whatever went wrong, the app keeps serving
            log.exception("the first library index failed")
            self._state = State.ERROR
            self._error = "indexing failed unexpectedly; the server log has the details"

    # -- indexing

    async def refresh(self) -> Snapshot:
        """Sync every source and index the result. Callers queue, not overlap."""
        async with self._lock:
            if not self._sources:
                return self._snapshot

            snapshot = await asyncio.to_thread(self._index)

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

    def _index(self) -> Snapshot:
        """Blocking: runs git and reads every file. Called off the event loop."""
        started = time.perf_counter()

        outcomes: list[Checkout | SyncFailure] = []
        for source in self._sources:
            try:
                outcomes.append(self._sync(source, self._workdir))
            except SourceError as exc:
                log.warning("source %s failed to sync: %s", source.id, exc)
                outcomes.append(SyncFailure(source, str(exc)))

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

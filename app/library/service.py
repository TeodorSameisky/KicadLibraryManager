"""Keeps a current snapshot of the library available while the app runs.

The app starts at once, reports that it is indexing, and swaps the finished
snapshot in when it lands: cloning a library takes longer than a container is
given to become healthy.

After that the library is looked at again whenever something says it may have
changed -- a webhook from the git host, a librarian pressing a button -- and
on a timer for when nothing says so.

What this holds is the snapshot being served and the one lock that keeps a
refresh and a change from touching a checkout at the same time. The work
itself is done by others:

    indexer   fetching the sources and reading them into a snapshot
    writer    changing the repository: commit, push, again if somebody was first
    history   what was changed in a file, remembered until the library moves on
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path

from app.library.history import DEFAULT_LIMIT, Histories, History, HistoryUnavailable
from app.library.indexer import Indexer
from app.library.publish import Identity, Publisher, PublishError
from app.library.snapshot import Snapshot
from app.library.sources import Checkout, Source, sync
from app.library.writer import Write, Writer

log = logging.getLogger(__name__)

Sync = Callable[[Source, Path], Checkout]

__all__ = ["LibraryService", "State", "Sync", "Write"]


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
        publisher: Publisher | None = None,
        token: str | None = None,
    ) -> None:
        self._sources = tuple(sources)
        self._workdir = workdir
        self._sync = sync
        self._interval = refresh_interval_seconds

        self._indexer = Indexer(self._sources, self._fetch)
        # Only with a publisher: without one the library is read and not changed.
        self._writer = Writer(publisher, self._fetch) if publisher is not None else None
        # The token is for reading the old versions of files in a private
        # repository. The clones themselves are made by `sync`, which is
        # given the same token.
        self._histories = Histories(token)

        self._snapshot = Snapshot()
        self._state = State.SYNCING if self._sources else State.EMPTY
        self._error: str | None = None
        self._checked_at: float | None = None
        # Where each source's checkout is on disk. Kept here, and not in the
        # snapshot, because the snapshot is shown to people and a server path
        # is not for them.
        self._checkouts: dict[str, Path] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._requested: asyncio.Task[None] | None = None

    def _fetch(self, source: Source) -> Checkout:
        """Bring one source up to date. The one way a checkout is reached,
        for reading and for writing alike."""
        return self._sync(source, self._workdir)

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

    @property
    def sources(self) -> tuple[Source, ...]:
        """The repositories the library is read from, in the order configured."""
        return self._sources

    @property
    def writable(self) -> bool:
        """Whether this deployment can change the library, not only read it."""
        return self._writer is not None and bool(self._sources)

    def _parts_source(self) -> Source | None:
        """The source the parts are read from, and so the one a new part goes to."""
        wanted = self._snapshot.parts_source
        return next((s for s in self._sources if s.id == wanted), None)

    def history(self, relative: str, limit: int = DEFAULT_LIMIT) -> History:
        """The changes made to one file of the library, newest first.

        Blocking: it runs git.
        """
        snapshot = self._snapshot
        root = self._checkouts.get(snapshot.parts_source or "")
        if root is None:
            raise HistoryUnavailable("the library has not been indexed yet")

        commit = next((s.commit for s in snapshot.sources if s.id == snapshot.parts_source), None)
        return self._histories.read(root, self._parts_source(), commit, relative, limit)

    # -- keeping watch

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

    # -- changing and refreshing

    async def change(self, write: Write, author: Identity) -> Snapshot:
        """Make a change to the library and publish it.

        `write` changes the files of a checkout and returns the commit
        message; the change is committed under `author` and pushed. The
        library is then indexed again, so what is served is what was just
        written.

        Holds the same lock as a refresh: nothing else touches the checkout
        while it is being written.
        """
        if self._writer is None:
            raise PublishError("this deployment is not set up to change the library")
        async with self._lock:
            # The source the parts are read from; before the first index
            # there is no saying which that is, so the first one configured.
            source = self._parts_source() or self._sources[0]
            await asyncio.to_thread(self._writer.change, source, write, author)
        return await self.refresh()

    async def refresh(self) -> Snapshot:
        """Sync every source and index the result. Callers queue, not overlap."""
        async with self._lock:
            if not self._sources:
                return self._snapshot

            indexed = await asyncio.to_thread(self._indexer.index, self._snapshot)
            snapshot = indexed.snapshot
            self._checkouts = indexed.checkouts
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

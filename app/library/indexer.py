"""Reading every source into one snapshot.

Indexing means bringing every source up to date and reading every file in it.
For a repository carrying 3D models that takes long enough to matter, so it is
blocking work, done off the event loop, and not done at all when nothing has
moved: looking is cheap, reading is not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from app.library.snapshot import Snapshot, SyncFailure, build_snapshot
from app.library.sources import Checkout, Source, SourceError

log = logging.getLogger(__name__)

# Bring one source up to date, and say where its files are and at which commit.
Fetch = Callable[[Source], Checkout]


@dataclass(frozen=True)
class Indexed:
    # The snapshot given, itself, when no source had moved: the caller can
    # then tell without comparing anything.
    snapshot: Snapshot
    # Where each source that answered is on disk, by source id. Apart from
    # the snapshot, because the snapshot is shown to people and a server path
    # is not for them.
    checkouts: dict[str, Path]


class Indexer:
    def __init__(self, sources: Sequence[Source], fetch: Fetch) -> None:
        self._sources = tuple(sources)
        self._fetch = fetch

    def index(self, current: Snapshot) -> Indexed:
        """Fetch every source, and read them if any has moved.

        Blocking: runs git and reads every file. A source that cannot be
        reached is not an error here: it is in the snapshot, as a source that
        failed, for the caller to decide what to serve.
        """
        started = time.perf_counter()

        outcomes: list[Checkout | SyncFailure] = []
        for source in self._sources:
            try:
                outcomes.append(self._fetch(source))
            except SourceError as exc:
                log.warning("source %s failed to sync: %s", source.id, exc)
                outcomes.append(SyncFailure(source, str(exc)))

        checkouts = {o.source.id: o.path for o in outcomes if isinstance(o, Checkout)}

        # Every source at the commit that is already indexed: there is nothing
        # new to read. A failure anywhere is always worth a fresh look.
        commits = [o.commit if isinstance(o, Checkout) else None for o in outcomes]
        indexed = current.indexed_at is not None
        if indexed and None not in commits and commits == [s.commit for s in current.sources]:
            return Indexed(current, checkouts)

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
        return Indexed(snapshot, checkouts)

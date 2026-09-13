"""Holds the indexed library and keeps it current.

Synchronising means cloning or fetching every source and walking it, which for
a repository carrying 3D models takes long enough that it cannot happen during
startup -- the container would fail its health check while git is still
running. So the app starts immediately, serves a documented "indexing" state,
and swaps in the finished index when it is ready.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.kicad.catalog import Catalog
from app.kicad.issues import Issue, errors, warnings
from app.kicad.library import index_repository
from app.kicad.parts import (
    PART_STATUSES,
    Part,
    PartsIndex,
    check_against_catalog,
    load_parts,
)
from app.kicad.sources import Source, SourceError, SyncResult, load_sources, sync

log = logging.getLogger(__name__)


class State(str, Enum):
    EMPTY = "empty"  # nothing configured
    SYNCING = "syncing"  # first index in progress
    READY = "ready"
    ERROR = "error"  # nothing usable was produced


@dataclass
class SourceStatus:
    id: str
    name: str
    commit: str | None = None
    symbols: int = 0
    parts: int = 0
    error: str | None = None


@dataclass
class Snapshot:
    """An indexed view of every source. Replaced wholesale, never mutated.

    Swapping a finished snapshot in means readers never observe a half-built
    index, without any locking on the read path.
    """

    catalog: Catalog = field(default_factory=Catalog)
    parts: PartsIndex | None = None
    sources: list[SourceStatus] = field(default_factory=list)
    finished_at: float | None = None
    duration: float = 0.0

    @property
    def issues(self) -> list[Issue]:
        out = list(self.catalog.all_issues)
        if self.parts:
            out.extend(self.parts.issues)
        return out

    @property
    def errors(self) -> list[Issue]:
        return errors(self.issues)

    @property
    def warnings(self) -> list[Issue]:
        return warnings(self.issues)

    def part_count(self) -> int:
        return len(self.parts.parts) if self.parts else 0

    def facets(self) -> dict[str, list[dict]]:
        """What the catalog can be filtered by, with counts.

        Sent alongside the index state so the panel can offer filters that
        reflect the library in front of it. A category with no parts is
        omitted: offering a filter that can only ever return nothing is worse
        than not offering it.
        """
        if self.parts is None:
            return {"statuses": [], "categories": []}

        by_status: Counter[str] = Counter()
        by_category: Counter[str] = Counter()
        for part in self.parts.parts.values():
            by_status[part.status] += 1
            if part.category:
                by_category[part.category] += 1

        names = self.parts.categories
        return {
            "statuses": [
                {"value": status, "label": status, "count": by_status[status]}
                for status in PART_STATUSES
                if by_status[status]
            ],
            "categories": [
                {
                    "value": code,
                    "label": names[code].name if code in names else code,
                    "count": count,
                }
                for code, count in sorted(by_category.items())
            ],
        }


class LibraryService:
    def __init__(self, sources: list[Source], workdir: Path) -> None:
        self._sources = sources
        self._workdir = workdir
        self._snapshot = Snapshot()
        self._state = State.EMPTY if not sources else State.SYNCING
        self._error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    @property
    def sources(self) -> list[Source]:
        return list(self._sources)

    async def refresh(self) -> Snapshot:
        """Re-sync and re-index. Concurrent callers share one run."""
        async with self._lock:
            if not self._sources:
                self._state = State.EMPTY
                return self._snapshot

            if self._state is not State.READY:
                self._state = State.SYNCING

            snapshot = await asyncio.to_thread(self._build)

            if snapshot.sources and all(s.error for s in snapshot.sources):
                # Keep serving the previous index rather than going blank.
                self._state = State.ERROR if self._snapshot.finished_at is None else State.READY
                self._error = "; ".join(f"{s.id}: {s.error}" for s in snapshot.sources if s.error)
                return self._snapshot

            self._snapshot = snapshot
            self._state = State.READY
            self._error = None
            return snapshot

    def _build(self) -> Snapshot:
        """Blocking: clone/fetch and walk every source."""
        started = time.perf_counter()
        snapshot = Snapshot()
        synced: list[SyncResult] = []

        for source in self._sources:
            status = SourceStatus(id=source.id, name=source.name)
            try:
                result = sync(source, self._workdir)
                synced.append(result)
                status.commit = result.commit
            except SourceError as exc:
                status.error = str(exc)
                log.warning("source %s failed to sync: %s", source.id, exc)
                snapshot.sources.append(status)
                continue

            index = index_repository(result.path)
            snapshot.catalog.add(source.id, index)
            status.symbols = len(index.symbols)
            snapshot.sources.append(status)

        snapshot.catalog.check_references()

        # Parts may live in any source; the first one providing them wins.
        for result in synced:
            if not (result.path / "parts").is_dir():
                continue
            parts = load_parts(result.path)
            check_against_catalog(parts, snapshot.catalog)
            snapshot.parts = parts
            for status in snapshot.sources:
                if status.id == result.source.id:
                    status.parts = len(parts.parts)
            break

        snapshot.duration = time.perf_counter() - started
        snapshot.finished_at = time.time()
        log.info(
            "indexed %d source(s): %d symbols, %d parts, %d errors in %.1fs",
            len(snapshot.sources),
            snapshot.catalog.symbol_count(),
            snapshot.part_count(),
            len(snapshot.errors),
            snapshot.duration,
        )
        return snapshot

    # -- queries ----------------------------------------------------------

    def search(
        self,
        query: str = "",
        status: str | None = None,
        category: str | None = None,
    ) -> list[Part]:
        """Every part matching the filters, ordered by IPN.

        The text match is a substring over IPN, description, fields and MPNs.
        The numbers carry no meaning, so searching only the IPN would be
        useless; description and fields are what people actually know.

        Returns the whole match rather than a page of it, so the caller can
        report how many there were before deciding how many to send.
        """
        parts = self._snapshot.parts
        if parts is None:
            return []

        results = sorted(parts.parts.values(), key=lambda p: p.ipn)

        if status:
            results = [p for p in results if p.status == status]
        if category:
            results = [p for p in results if p.category == category]

        needle = query.strip().lower()
        if not needle:
            return results

        def haystack(part: Part) -> str:
            bits = [part.ipn, part.description, *part.fields.values()]
            bits += [r.mpn for r in part.mpns]
            bits += [parts.mpns[r.mpn].manufacturer for r in part.mpns if r.mpn in parts.mpns]
            return " ".join(bits).lower()

        return [p for p in results if needle in haystack(p)]

    def get_part(self, ipn: str) -> Part | None:
        parts = self._snapshot.parts
        return parts.parts.get(ipn) if parts else None

    def parts_sharing_mpn(self, part: Part) -> list[Part]:
        """Other IPNs that list any of this part's manufacturer parts.

        The question asked when a manufacturer discontinues something, and the
        reason MPNs are separate documents rather than embedded in each IPN.
        """
        parts = self._snapshot.parts
        if parts is None:
            return []

        related: dict[str, Part] = {}
        for ref in part.mpns:
            for other in parts.parts_using(ref.mpn):
                if other.ipn != part.ipn:
                    related[other.ipn] = other
        return sorted(related.values(), key=lambda p: p.ipn)


def build_service(raw_sources: str | None, workdir: Path) -> LibraryService:
    try:
        sources = load_sources(raw_sources)
    except SourceError as exc:
        log.error("library sources are misconfigured: %s", exc)
        sources = []
    return LibraryService(sources=sources, workdir=workdir)

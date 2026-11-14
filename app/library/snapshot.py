"""One finished index of every source, and the questions asked of it.

A snapshot is built whole and never changed afterwards. Replacing the current
one with the next means a reader never sees a half-built index, without any
locking on the read path.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property

from app.library.assets import index_repository
from app.library.catalog import Catalog, asset_issues, part_issues
from app.library.issues import Issue, Severity, errors, warnings
from app.library.parts import PART_STATUSES, Mpn, Part, PartsIndex, has_parts, load_parts
from app.library.sources import Checkout, Source


@dataclass(frozen=True)
class SyncFailure:
    """A source that could not be cloned or updated this time round."""

    source: Source
    error: str


@dataclass(frozen=True)
class SourceStatus:
    id: str
    name: str
    commit: str | None = None
    symbols: int = 0
    footprints: int = 0
    models: int = 0
    parts: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Facet:
    """One value a list can be narrowed to, and how many parts it would leave."""

    value: str
    label: str
    count: int


@dataclass(frozen=True)
class Snapshot:
    catalog: Catalog = field(default_factory=Catalog)
    parts: PartsIndex = field(default_factory=PartsIndex)
    sources: tuple[SourceStatus, ...] = ()
    issues: tuple[Issue, ...] = ()
    indexed_at: float | None = None  # epoch seconds; None until an index lands
    duration: float = 0.0

    @cached_property
    def errors(self) -> list[Issue]:
        return errors(self.issues)

    @cached_property
    def warnings(self) -> list[Issue]:
        return warnings(self.issues)

    @property
    def part_count(self) -> int:
        return len(self.parts.parts)

    @property
    def failed(self) -> bool:
        """Every source failed, so there is nothing here worth serving."""
        return bool(self.sources) and all(source.error for source in self.sources)

    # -- parts

    def part(self, ipn: str) -> Part | None:
        return self.parts.parts.get(ipn)

    def mpn(self, number: str | None) -> Mpn | None:
        return self.parts.mpns.get(number) if number else None

    def category_name(self, code: str) -> str:
        category = self.parts.categories.get(code)
        return category.name if category else code

    @cached_property
    def _search_text(self) -> dict[str, str]:
        """What each part is matched against, by IPN.

        IPN numbers carry no meaning, so matching only those would be
        useless. The description, the field values and the manufacturer parts
        are what people actually know about the thing they are looking for.
        """
        text: dict[str, str] = {}
        for part in self.parts.parts.values():
            words = [part.ipn, part.description, *part.fields.values()]
            for ref in part.mpns:
                words.append(ref.mpn)
                mpn = self.parts.mpns.get(ref.mpn)
                if mpn is not None:
                    words.append(mpn.manufacturer)
            text[part.ipn] = " ".join(words).lower()
        return text

    def search(
        self, query: str = "", *, status: str | None = None, category: str | None = None
    ) -> list[Part]:
        """Every part matching the filters, ordered by IPN.

        The whole match rather than a page of it, so a caller can say how
        many there were before deciding how many to send.
        """
        needle = query.strip().lower()
        text = self._search_text
        return [
            part
            for part in sorted(self.parts.parts.values(), key=lambda part: part.ipn)
            if (status is None or part.status == status)
            and (category is None or part.category == category)
            and (not needle or needle in text[part.ipn])
        ]

    def related(self, part: Part) -> list[Part]:
        """Other IPNs that share a manufacturer part with this one.

        The reach of a discontinuation: if one of this part's sources goes
        away, these are the other parts that just lost a source too.
        """
        found: dict[str, Part] = {}
        for ref in part.mpns:
            for other in self.parts.parts_using(ref.mpn):
                if other.ipn != part.ipn:
                    found[other.ipn] = other
        return [found[ipn] for ipn in sorted(found)]

    # -- filters

    @cached_property
    def status_facets(self) -> list[Facet]:
        counts = Counter(part.status for part in self.parts.parts.values())
        return [Facet(s, s, counts[s]) for s in PART_STATUSES if counts[s]]

    @cached_property
    def category_facets(self) -> list[Facet]:
        """Categories that have parts. One that can only ever return nothing
        is worse than not offering it."""
        counts = Counter(part.category for part in self.parts.parts.values() if part.category)
        return [Facet(code, self.category_name(code), counts[code]) for code in sorted(counts)]


def build_snapshot(outcomes: Sequence[Checkout | SyncFailure]) -> Snapshot:
    """Index what was checked out, in the order the sources are configured.

    Blocking: it reads every file of every repository.
    """
    catalog = Catalog()
    statuses: dict[str, SourceStatus] = {}
    checkouts: list[Checkout] = []

    for outcome in outcomes:
        source = outcome.source
        if isinstance(outcome, SyncFailure):
            statuses[source.id] = SourceStatus(source.id, source.name, error=outcome.error)
            continue

        index = index_repository(outcome.path)
        catalog.indexes[source.id] = index
        checkouts.append(outcome)
        statuses[source.id] = SourceStatus(
            id=source.id,
            name=source.name,
            commit=outcome.commit,
            symbols=len(index.symbols),
            footprints=len(index.footprints),
            models=len(index.models),
        )

    issues = asset_issues(catalog)
    parts = PartsIndex()

    # IPNs are one numbering scheme, so they come from one repository: the
    # first source that has any.
    providers = [checkout for checkout in checkouts if has_parts(checkout.path)]
    if providers:
        provider = providers[0].source
        parts = load_parts(providers[0].path)
        issues.extend(part_issues(parts, catalog, provider.id))

        statuses[provider.id] = replace(statuses[provider.id], parts=len(parts.parts))
        for ignored in providers[1:]:
            issues.append(
                Issue.warning(
                    "parts-ignored",
                    f"parts are read from {provider.id!r} only; the parts/ directory "
                    "in this source is not used",
                    "parts",
                ).from_source(ignored.source.id)
            )

    # Errors first, otherwise in the order they were found: a list cut short
    # by a page limit should lose advice before it loses repairs.
    issues.sort(key=lambda issue: issue.severity is not Severity.ERROR)

    return Snapshot(
        catalog=catalog,
        parts=parts,
        sources=tuple(statuses[outcome.source.id] for outcome in outcomes),
        issues=tuple(issues),
    )

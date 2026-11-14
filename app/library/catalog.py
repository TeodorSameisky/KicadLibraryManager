"""Every source's assets, looked up as one library.

A reference legitimately crosses repositories. KiCad's own libraries are the
clearest case -- symbols, footprints and 3D models are three repositories --
and a company library that reuses stock footprints is the same situation. So
lookups, and the checks that depend on them, happen here rather than per
repository.

When several sources provide the same asset, the first one configured wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.library.assets import AssetIndex, BrokenInheritance, Footprint, Model, Symbol
from app.library.issues import Issue
from app.library.parts import PartsIndex
from app.library.refs import LibraryRef, model_key


@dataclass
class Catalog:
    # Keyed by source id, in the order the sources are configured.
    indexes: dict[str, AssetIndex] = field(default_factory=dict)

    # -- lookups

    def symbol(self, ref: LibraryRef) -> Symbol | None:
        located = self._locate_symbol(ref)
        return located[1] if located else None

    def footprint(self, ref: LibraryRef) -> Footprint | None:
        for index in self.indexes.values():
            found = index.footprints.get((ref.library, ref.name))
            if found is not None:
                return found
        return None

    def model(self, key: str) -> Model | None:
        for index in self.indexes.values():
            found = index.models.get(key)
            if found is not None:
                return found
        return None

    def footprint_sources(self, ref: LibraryRef) -> list[str]:
        """Every source that provides a footprint, in lookup order."""
        return [
            source
            for source, index in self.indexes.items()
            if (ref.library, ref.name) in index.footprints
        ]

    def symbol_ancestry(self, ref: LibraryRef) -> list[Symbol]:
        """A symbol and everything it extends, root ancestor last.

        Raises LookupError when no source provides the symbol, and
        BrokenInheritance when its chain does not reach a body.
        """
        located = self._locate_symbol(ref)
        if located is None:
            raise LookupError(f"symbol {str(ref)!r} is not provided by any source")
        index, symbol = located
        # `extends` names a symbol in the same library, so the chain is
        # resolved inside the repository the symbol came from.
        return index.ancestry(symbol)

    def _locate_symbol(self, ref: LibraryRef) -> tuple[AssetIndex, Symbol] | None:
        for index in self.indexes.values():
            found = index.symbols.get((ref.library, ref.name))
            if found is not None:
                return index, found
        return None

    # -- totals

    @property
    def symbol_count(self) -> int:
        return sum(len(index.symbols) for index in self.indexes.values())

    @property
    def footprint_count(self) -> int:
        return sum(len(index.footprints) for index in self.indexes.values())

    @property
    def model_count(self) -> int:
        return sum(len(index.models) for index in self.indexes.values())


# -- checks that need every source ------------------------------------------


def asset_issues(catalog: Catalog) -> list[Issue]:
    """What each repository reported, plus the references that span them."""
    found: list[Issue] = []

    for source, index in catalog.indexes.items():
        found.extend(issue.from_source(source) for issue in index.issues)

        for symbol in index.symbols.values():
            ref = LibraryRef.parse(symbol.footprint)
            if ref is None:
                continue  # unset, or unqualified and already reported

            providers = catalog.footprint_sources(ref)
            if not providers:
                found.append(
                    Issue.error(
                        "missing-footprint",
                        f"symbol {symbol.name!r} names footprint {str(ref)!r}, "
                        "which no source provides",
                        index.relative(symbol.path),
                    ).from_source(source)
                )
            elif len(providers) > 1:
                # Picking one silently would make what gets placed depend on
                # the order the sources happen to be listed in.
                found.append(
                    Issue.warning(
                        "ambiguous-footprint",
                        f"footprint {str(ref)!r} is provided by several sources "
                        f"({', '.join(providers)}); {providers[0]} is used",
                        index.relative(symbol.path),
                    ).from_source(source)
                )

        for footprint in index.footprints.values():
            for reference in footprint.model_refs:
                key = model_key(reference)
                if catalog.model(key) is None:
                    found.append(
                        Issue.error(
                            "missing-model",
                            f"footprint {footprint.name!r} names 3D model {key!r}, "
                            "which no source provides",
                            index.relative(footprint.path),
                        ).from_source(source)
                    )

    return found


def part_issues(parts: PartsIndex, catalog: Catalog, source: str) -> list[Issue]:
    """What the parts loader reported, plus each part's assets resolved."""
    found = [issue.from_source(source) for issue in parts.issues]

    def report(kind: str, message: str, path: str) -> None:
        found.append(Issue.error(kind, message, path).from_source(source))

    for part in parts.parts.values():
        if part.symbol:
            ref = LibraryRef.parse(part.symbol)
            if ref is None:
                report(
                    "unqualified-symbol",
                    f"symbol {part.symbol!r} is not <library>:<name>",
                    part.path,
                )
            else:
                try:
                    catalog.symbol_ancestry(ref)
                except LookupError:
                    report(
                        "missing-symbol",
                        f"{part.ipn} names symbol {part.symbol!r}, which no source provides",
                        part.path,
                    )
                except BrokenInheritance as exc:
                    # Reported against the symbol too, but this is the line
                    # that says which part cannot be placed because of it.
                    report("unplaceable", f"{part.ipn} cannot be placed: {exc}", part.path)

        if part.footprint:
            ref = LibraryRef.parse(part.footprint)
            if ref is None:
                report(
                    "unqualified-footprint",
                    f"footprint {part.footprint!r} is not <library>:<name>",
                    part.path,
                )
            elif catalog.footprint(ref) is None:
                report(
                    "missing-footprint",
                    f"{part.ipn} names footprint {part.footprint!r}, which no source provides",
                    part.path,
                )

    return found

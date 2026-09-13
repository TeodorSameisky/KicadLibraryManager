"""The catalog: every indexed source, and the references that span them.

Footprint and 3D model resolution lives here rather than in the per-repository
indexer because a reference legitimately crosses repositories. KiCad's own
libraries are the clearest case -- symbols, footprints and 3D models are three
separate repositories -- and a company library that reuses stock KiCad
footprints is the same situation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.kicad.issues import Issue, Severity, errors, warnings
from app.kicad.library import (
    Footprint,
    LibraryIndex,
    Model,
    Symbol,
    normalise_model_ref,
)


@dataclass(frozen=True)
class Located:
    """An asset together with the source it came from."""

    source_id: str
    asset: Symbol | Footprint | Model


@dataclass
class Catalog:
    indexes: dict[str, LibraryIndex] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def add(self, source_id: str, index: LibraryIndex) -> None:
        self.indexes[source_id] = index

    # -- lookups ----------------------------------------------------------

    def find_symbol(self, library: str, name: str) -> list[Located]:
        return [
            Located(sid, idx.symbols[(library, name)])
            for sid, idx in self.indexes.items()
            if (library, name) in idx.symbols
        ]

    def find_footprint(self, library: str, name: str) -> list[Located]:
        return [
            Located(sid, idx.footprints[(library, name)])
            for sid, idx in self.indexes.items()
            if (library, name) in idx.footprints
        ]

    def find_model(self, key: str) -> list[Located]:
        return [
            Located(sid, idx.models[key]) for sid, idx in self.indexes.items() if key in idx.models
        ]

    # -- reporting --------------------------------------------------------

    @property
    def all_issues(self) -> list[Issue]:
        """Per-source issues followed by the cross-source ones."""
        out: list[Issue] = []
        for index in self.indexes.values():
            out.extend(index.issues)
        out.extend(self.issues)
        return out

    @property
    def errors(self) -> list[Issue]:
        return errors(self.all_issues)

    @property
    def warnings(self) -> list[Issue]:
        return warnings(self.all_issues)

    def symbol_count(self) -> int:
        return sum(len(i.symbols) for i in self.indexes.values())

    # -- cross-source resolution ------------------------------------------

    def check_references(self) -> None:
        """Resolve every cross-repository reference, recording what dangles."""
        self.issues.clear()

        for source_id, index in self.indexes.items():
            for symbol in index.symbols.values():
                rel = symbol.path.relative_to(index.root).as_posix()
                fp_ref = symbol.properties.get("Footprint", "")
                if not fp_ref or ":" not in fp_ref:
                    continue  # absent or malformed; reported per-source already

                fp_lib, fp_name = fp_ref.split(":", 1)
                found = self.find_footprint(fp_lib, fp_name)

                if not found:
                    self.issues.append(
                        Issue(
                            Severity.ERROR,
                            "missing-footprint",
                            f"[{source_id}] {symbol.name!r} references footprint "
                            f"{fp_ref!r}, which no source provides",
                            rel,
                        )
                    )
                elif len(found) > 1:
                    where = ", ".join(sorted(f.source_id for f in found))
                    self.issues.append(
                        Issue(
                            Severity.WARNING,
                            "ambiguous-footprint",
                            f"[{source_id}] footprint {fp_ref!r} is provided by "
                            f"several sources ({where}); the first wins",
                            rel,
                        )
                    )

            for fp in index.footprints.values():
                rel = fp.path.relative_to(index.root).as_posix()
                for ref in fp.model_refs:
                    key = normalise_model_ref(ref)
                    if not self.find_model(key):
                        self.issues.append(
                            Issue(
                                Severity.ERROR,
                                "missing-model",
                                f"[{source_id}] {fp.name!r} references 3D model "
                                f"{key!r}, which no source provides",
                                rel,
                            )
                        )

    def summary(self) -> str:
        lines = [
            f"{len(self.indexes)} source(s), {self.symbol_count()} symbol(s), "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        ]
        for source_id, index in self.indexes.items():
            derived = sum(1 for s in index.symbols.values() if s.is_derived)
            lines.append(
                f"  {source_id}: {len(index.symbols)} symbols ({derived} derived), "
                f"{len(index.footprints)} footprints, {len(index.models)} models"
            )
        return "\n".join(lines)

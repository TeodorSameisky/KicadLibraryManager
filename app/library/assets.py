"""One repository's symbols, footprints and 3D models.

Walks the three asset trees and records what is there and what is wrong with
it. Only checks that can be settled inside the repository happen here. Whether
a symbol's footprint exists is not one of them: KiCad's own libraries keep
symbols, footprints and models in three separate repositories, so a reference
that dangles here may well resolve against another source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.kicad import symbol as symbol_tree
from app.kicad.sexpr import ParseError, SExpr, loads
from app.library.issues import Issue

SYMBOL_DIR_SUFFIX = ".kicad_symdir"
FOOTPRINT_DIR_SUFFIX = ".pretty"
MODEL_DIR_SUFFIX = ".3dshapes"
MODEL_EXTENSIONS = (".step", ".stp", ".wrl")

# What a clone holds in place of a file whose content was never fetched.
_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
_LFS_POINTER_MAX_BYTES = 512


class BrokenInheritance(ValueError):
    """A symbol's `extends` chain does not lead to a symbol with a body."""


@dataclass(frozen=True)
class Symbol:
    library: str
    name: str
    path: Path
    extends: str | None
    footprint: str  # the symbol's own Footprint property, "" when unset


@dataclass(frozen=True)
class Footprint:
    library: str
    name: str
    path: Path
    model_refs: tuple[str, ...]  # as written in the file, variables and all


@dataclass(frozen=True)
class Model:
    library: str
    name: str  # the file name, extension included
    path: Path
    is_lfs_pointer: bool

    @property
    def key(self) -> str:
        """How footprints name it: `Passives.3dshapes/R_0603.step`."""
        return f"{self.library}{MODEL_DIR_SUFFIX}/{self.name}"


@dataclass
class AssetIndex:
    root: Path
    symbols: dict[tuple[str, str], Symbol] = field(default_factory=dict)
    footprints: dict[tuple[str, str], Footprint] = field(default_factory=dict)
    models: dict[str, Model] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def ancestry(self, symbol: Symbol) -> list[Symbol]:
        """A symbol followed by everything it extends, root ancestor last.

        A derived symbol has no graphics of its own; they are its root
        ancestor's. So a chain that does not reach one is not a smaller
        problem than a missing symbol -- the part would place with no body
        and no pins.
        """
        chain = [symbol]
        seen = {symbol.name}

        while chain[-1].extends is not None:
            parent_name = chain[-1].extends
            if parent_name in seen:
                trail = " -> ".join([s.name for s in chain] + [parent_name])
                raise BrokenInheritance(f"extends cycle in {symbol.library}: {trail}")

            parent = self.symbols.get((symbol.library, parent_name))
            if parent is None:
                raise BrokenInheritance(
                    f"{chain[-1].name!r} extends {parent_name!r}, "
                    f"which is not in {symbol.library}"
                )
            seen.add(parent_name)
            chain.append(parent)

        return chain


# -- walking ---------------------------------------------------------------


def _library_dirs(root: Path, group: str, suffix: str) -> list[tuple[str, Path]]:
    """(library name, directory) pairs for one kind of asset.

    A self-contained repository groups by kind -- symbols/, footprints/,
    3dmodels/ -- while KiCad's own libraries are one repository per kind with
    the library directories at the root. Both are read.
    """
    found = sorted(root.glob(f"{group}/*{suffix}")) or sorted(root.glob(f"*{suffix}"))
    return [(path.name[: -len(suffix)], path) for path in found if path.is_dir()]


def _read_tree(path: Path, expected_head: str, index: AssetIndex) -> SExpr | None:
    """Parse one file, recording why if it cannot be used."""
    relative = index.relative(path)
    try:
        tree = loads(path.read_text(encoding="utf-8"))
    except (ParseError, UnicodeDecodeError, OSError) as exc:
        index.issues.append(Issue.error("unparseable", str(exc), relative))
        return None

    if tree.head != expected_head:
        index.issues.append(
            Issue.error(
                "unexpected-content",
                f"file starts with {tree.head!r}, expected {expected_head!r}",
                relative,
            )
        )
        return None
    return tree


def _index_symbols(index: AssetIndex) -> None:
    for library, directory in _library_dirs(index.root, "symbols", SYMBOL_DIR_SUFFIX):
        for path in sorted(directory.glob("*.kicad_sym")):
            relative = index.relative(path)
            tree = _read_tree(path, "kicad_symbol_lib", index)
            if tree is None:
                continue

            definitions = list(tree.children("symbol"))
            if not definitions:
                index.issues.append(Issue.error("empty", "contains no symbol", relative))
                continue
            if len(definitions) > 1:
                index.issues.append(
                    Issue.warning(
                        "multiple-symbols",
                        f"{len(definitions)} symbols in one file; an unpacked library "
                        "holds one per file, and only the first is used",
                        relative,
                    )
                )

            node = definitions[0]
            name = symbol_tree.symbol_name(node)
            if not name:
                index.issues.append(Issue.error("unnamed", "symbol has no name", relative))
                continue
            if name != path.stem:
                index.issues.append(
                    Issue.warning(
                        "name-mismatch", f"symbol {name!r} is in file {path.stem!r}", relative
                    )
                )
            if (library, name) in index.symbols:
                index.issues.append(
                    Issue.error(
                        "duplicate-symbol", f"{library}:{name} is already defined", relative
                    )
                )
                continue

            index.symbols[(library, name)] = Symbol(
                library=library,
                name=name,
                path=path,
                extends=symbol_tree.extends(node),
                footprint=symbol_tree.properties(node).get("Footprint", ""),
            )


def _index_footprints(index: AssetIndex) -> None:
    for library, directory in _library_dirs(index.root, "footprints", FOOTPRINT_DIR_SUFFIX):
        for path in sorted(directory.glob("*.kicad_mod")):
            relative = index.relative(path)
            tree = _read_tree(path, "footprint", index)
            if tree is None:
                continue

            name = tree.arg(0) or path.stem
            if name != path.stem:
                index.issues.append(
                    Issue.warning(
                        "name-mismatch", f"footprint {name!r} is in file {path.stem!r}", relative
                    )
                )
            if (library, name) in index.footprints:
                index.issues.append(
                    Issue.error(
                        "duplicate-footprint", f"{library}:{name} is already defined", relative
                    )
                )
                continue

            index.footprints[(library, name)] = Footprint(
                library=library,
                name=name,
                path=path,
                model_refs=tuple(
                    ref for ref in (model.arg(0) for model in tree.children("model")) if ref
                ),
            )


def _is_lfs_pointer(path: Path) -> bool:
    try:
        if path.stat().st_size > _LFS_POINTER_MAX_BYTES:
            return False
        return path.read_text(encoding="utf-8", errors="ignore").startswith(_LFS_POINTER_PREFIX)
    except OSError:
        return False


def _index_models(index: AssetIndex) -> None:
    for library, directory in _library_dirs(index.root, "3dmodels", MODEL_DIR_SUFFIX):
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in MODEL_EXTENSIONS:
                continue

            model = Model(
                library=library, name=path.name, path=path, is_lfs_pointer=_is_lfs_pointer(path)
            )
            if model.is_lfs_pointer:
                # Sent to KiCad, a pointer gives an empty 3D view and no error
                # anywhere, so it is recorded as one here.
                index.issues.append(
                    Issue.error(
                        "lfs-pointer",
                        "file is a Git LFS pointer, not the model; the clone needs git lfs pull",
                        index.relative(path),
                    )
                )
            index.models[model.key] = model


def _check_symbols(index: AssetIndex) -> None:
    for symbol in index.symbols.values():
        relative = index.relative(symbol.path)

        if symbol.extends is not None:
            try:
                index.ancestry(symbol)
            except BrokenInheritance as exc:
                index.issues.append(Issue.error("broken-inheritance", str(exc), relative))

        if symbol.footprint and ":" not in symbol.footprint:
            index.issues.append(
                Issue.warning(
                    "unqualified-footprint",
                    f"footprint {symbol.footprint!r} has no library prefix",
                    relative,
                )
            )


def index_repository(root: Path) -> AssetIndex:
    """Index every asset under `root`."""
    index = AssetIndex(root=root)
    _index_symbols(index)
    _index_footprints(index)
    _index_models(index)
    _check_symbols(index)
    return index

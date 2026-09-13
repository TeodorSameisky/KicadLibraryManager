"""Index a KiCad library repository.

Walks the three mirrored asset trees, resolves the references between them, and
reports what does not line up. Resolution is deliberately separated from
reporting: an unresolved reference is data, not an exception, because a real
library always has some and the point is to list them rather than stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.kicad.sexpr import ParseError, SExpr, loads

SYMBOL_DIR_SUFFIX = ".kicad_symdir"
FOOTPRINT_DIR_SUFFIX = ".pretty"
MODEL_DIR_SUFFIX = ".3dshapes"

# A pointer file stands in for content that was never fetched; serving one to
# KiCad yields an empty 3D view with no error anywhere.
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
LFS_POINTER_MAX_BYTES = 512

_ENV_VAR = re.compile(r"^\$\{[^}]+\}[/\\]?")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    kind: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        where = f" [{self.path}]" if self.path else ""
        return f"{self.severity.value}: {self.message}{where}"


@dataclass
class Symbol:
    library: str
    name: str
    path: Path
    extends: str | None
    properties: dict[str, str]
    unit_count: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.library, self.name)

    @property
    def is_derived(self) -> bool:
        return self.extends is not None


@dataclass
class Footprint:
    library: str
    name: str
    path: Path
    model_refs: list[str]

    @property
    def key(self) -> tuple[str, str]:
        return (self.library, self.name)


@dataclass
class Model:
    library: str
    name: str
    path: Path
    is_lfs_pointer: bool

    @property
    def relative_key(self) -> str:
        """How footprints refer to it, e.g. Passives.3dshapes/R_0603.step"""
        return f"{self.library}{MODEL_DIR_SUFFIX}/{self.name}"


@dataclass
class LibraryIndex:
    root: Path
    symbols: dict[tuple[str, str], Symbol] = field(default_factory=dict)
    footprints: dict[tuple[str, str], Footprint] = field(default_factory=dict)
    models: dict[str, Model] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    def resolve_chain(self, library: str, name: str) -> list[Symbol]:
        """A derived symbol plus every ancestor, child first.

        KiCad needs the parent's graphics alongside a derived symbol; shipping
        the child alone places a part with no body and no pins.
        """
        chain: list[Symbol] = []
        seen: set[str] = set()
        current = self.symbols.get((library, name))

        while current is not None:
            if current.name in seen:
                trail = " -> ".join([s.name for s in chain] + [current.name])
                raise ValueError(f"extends cycle in {library}: {trail}")
            seen.add(current.name)
            chain.append(current)
            if current.extends is None:
                break
            current = self.symbols.get((library, current.extends))

        return chain


def _library_dirs(root: Path, subdir: str, suffix: str) -> list[Path]:
    """Library directories, whether grouped under a subdirectory or at the root.

    A self-contained repository groups by asset type (symbols/, footprints/,
    3dmodels/); KiCad's own libraries are one repository per type with the
    directories at the root. Both are worth supporting.
    """
    grouped = sorted(root.glob(subdir + "/*" + suffix))
    return grouped if grouped else sorted(root.glob("*" + suffix))


def _prop_map(node: SExpr) -> dict[str, str]:
    out: dict[str, str] = {}
    for prop in node.children("property"):
        atoms = prop.atoms()
        if atoms:
            out[atoms[0]] = atoms[1] if len(atoms) > 1 else ""
    return out


def _index_symbols(root: Path, index: LibraryIndex) -> None:
    for lib_dir in _library_dirs(root, "symbols", SYMBOL_DIR_SUFFIX):
        library = lib_dir.name[: -len(SYMBOL_DIR_SUFFIX)]

        for path in sorted(lib_dir.glob("*.kicad_sym")):
            rel = path.relative_to(root).as_posix()
            try:
                tree = loads(path.read_text(encoding="utf-8"))
            except (ParseError, UnicodeDecodeError) as exc:
                index.issues.append(Issue(Severity.ERROR, "unparseable", str(exc), rel))
                continue

            if tree.head != "kicad_symbol_lib":
                index.issues.append(
                    Issue(Severity.ERROR, "not-a-symbol-lib",
                          f"root is {tree.head!r}, expected kicad_symbol_lib", rel))
                continue

            defs = list(tree.children("symbol"))
            if not defs:
                index.issues.append(Issue(Severity.ERROR, "empty", "contains no symbol", rel))
                continue
            if len(defs) > 1:
                index.issues.append(
                    Issue(Severity.WARNING, "multiple-symbols",
                          f"{len(defs)} symbols in one file; unpacked format expects one", rel))

            node = defs[0]
            names = node.atoms()
            if not names:
                index.issues.append(Issue(Severity.ERROR, "unnamed", "symbol has no name", rel))
                continue
            name = names[0]

            if name != path.stem:
                index.issues.append(
                    Issue(Severity.WARNING, "name-mismatch",
                          f"symbol {name!r} in file {path.stem!r}", rel))

            extends_node = node.child("extends")
            extends = extends_node.atoms()[0] if extends_node and extends_node.atoms() else None

            symbol = Symbol(
                library=library,
                name=name,
                path=path,
                extends=extends,
                properties=_prop_map(node),
                unit_count=len(list(node.children("symbol"))),
            )
            if symbol.key in index.symbols:
                index.issues.append(
                    Issue(Severity.ERROR, "duplicate-symbol",
                          f"{library}:{name} already defined", rel))
                continue
            index.symbols[symbol.key] = symbol


def _index_footprints(root: Path, index: LibraryIndex) -> None:
    for lib_dir in _library_dirs(root, "footprints", FOOTPRINT_DIR_SUFFIX):
        library = lib_dir.name[: -len(FOOTPRINT_DIR_SUFFIX)]

        for path in sorted(lib_dir.glob("*.kicad_mod")):
            rel = path.relative_to(root).as_posix()
            try:
                tree = loads(path.read_text(encoding="utf-8"))
            except (ParseError, UnicodeDecodeError) as exc:
                index.issues.append(Issue(Severity.ERROR, "unparseable", str(exc), rel))
                continue

            if tree.head != "footprint":
                index.issues.append(
                    Issue(Severity.ERROR, "not-a-footprint",
                          f"root is {tree.head!r}, expected footprint", rel))
                continue

            names = tree.atoms()
            name = names[0] if names else path.stem
            if name != path.stem:
                index.issues.append(
                    Issue(Severity.WARNING, "name-mismatch",
                          f"footprint {name!r} in file {path.stem!r}", rel))

            refs = [m.atoms()[0] for m in tree.children("model") if m.atoms()]
            fp = Footprint(library=library, name=name, path=path, model_refs=refs)
            if fp.key in index.footprints:
                index.issues.append(
                    Issue(Severity.ERROR, "duplicate-footprint",
                          f"{library}:{name} already defined", rel))
                continue
            index.footprints[fp.key] = fp


def _index_models(root: Path, index: LibraryIndex) -> None:
    for lib_dir in _library_dirs(root, "3dmodels", MODEL_DIR_SUFFIX):
        library = lib_dir.name[: -len(MODEL_DIR_SUFFIX)]

        for path in sorted(lib_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in (".step", ".stp", ".wrl"):
                continue

            pointer = False
            if path.stat().st_size <= LFS_POINTER_MAX_BYTES:
                try:
                    head = path.read_text(encoding="utf-8", errors="ignore")
                    pointer = head.startswith(LFS_POINTER_PREFIX)
                except OSError:
                    pointer = False

            model = Model(library=library, name=path.name, path=path, is_lfs_pointer=pointer)
            if pointer:
                index.issues.append(
                    Issue(Severity.ERROR, "lfs-pointer",
                          "file is a Git LFS pointer; the clone needs git lfs pull",
                          path.relative_to(root).as_posix()))
            index.models[model.relative_key] = model


def normalise_model_ref(ref: str) -> str:
    """Strip any ${VAR} prefix, leaving a repository-relative path.

    Each contributor configures that variable differently, and it may not be
    defined at all on the server, so the variable is ignored and the remainder
    is resolved against the repository.
    """
    cleaned = ref.replace("\\", "/").strip()
    cleaned = _ENV_VAR.sub("", cleaned)
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def _check_references(index: LibraryIndex) -> None:
    for symbol in index.symbols.values():
        rel = symbol.path.relative_to(index.root).as_posix()

        if symbol.extends and (symbol.library, symbol.extends) not in index.symbols:
            index.issues.append(
                Issue(Severity.ERROR, "missing-parent",
                      f"{symbol.name!r} extends {symbol.extends!r}, "
                      f"which is not in {symbol.library}", rel))

        fp_ref = symbol.properties.get("Footprint", "")
        if not fp_ref:
            continue
        if ":" not in fp_ref:
            index.issues.append(
                Issue(Severity.WARNING, "unqualified-footprint",
                      f"footprint {fp_ref!r} has no library prefix", rel))
            continue

        fp_lib, fp_name = fp_ref.split(":", 1)
        if (fp_lib, fp_name) not in index.footprints:
            index.issues.append(
                Issue(Severity.ERROR, "missing-footprint",
                      f"{symbol.name!r} references footprint {fp_ref!r}, "
                      "which does not exist", rel))

    for fp in index.footprints.values():
        rel = fp.path.relative_to(index.root).as_posix()
        for ref in fp.model_refs:
            key = normalise_model_ref(ref)
            if key not in index.models:
                index.issues.append(
                    Issue(Severity.ERROR, "missing-model",
                          f"{fp.name!r} references 3D model {key!r}, "
                          "which does not exist", rel))


def index_repository(root: Path) -> LibraryIndex:
    """Index every asset under root and resolve the references between them."""
    index = LibraryIndex(root=root)
    _index_symbols(root, index)
    _index_footprints(root, index)
    _index_models(root, index)
    _check_references(index)
    return index

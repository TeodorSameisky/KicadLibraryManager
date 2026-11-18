"""Symbols, footprints and 3D models, added to the library from a browser.

A part names a symbol and a footprint; until now both had to be in the
library's repository already, put there with git. So a part that needed a new
package was asked for somewhere else. Here the drawings come in the way they
usually arrive -- a vendor's download, or what someone drew in KiCad's
editors: a `.kicad_sym`, a `.kicad_mod` and a STEP model, together.

An upload names one library, and each file goes where the library keeps that
kind of thing under that name:

    symbols/<Library>.kicad_symdir/<Name>.kicad_sym
    footprints/<Library>.pretty/<Name>.kicad_mod
    3dmodels/<Library>.3dshapes/<file>

Nothing here touches git. `plan_upload` reads the files and says what they
would add and replace, and what is wrong with them; `write_upload` puts a plan
into a checkout and names the commit. What is kept is what was uploaded,
changed only where it has to be:

- **A symbol file is split.** A library keeps one symbol per file, and a
  vendor's file may hold several. Each is written into a file of its own,
  with the version line of the file it came from, so KiCad reads it by the
  same rules.
- **A footprint is pointed at its 3D model.** A vendor's footprint names its
  model wherever the vendor kept it. If that model is uploaded with it, the
  footprint is made to name it where it is now going, through the variable
  the library's other footprints use. A footprint naming no model, uploaded
  with exactly one, is given it at its origin -- and that is said, since only
  KiCad's footprint editor shows whether it sits right.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from app.domain.refs import LibraryRef
from app.kicad import symbol as symbol_tree
from app.kicad.render import render_footprint, render_symbol
from app.kicad.sexpr import Atom, ParseError, SExpr, atom, dumps, loads, sexpr
from app.library.assets import (
    FOOTPRINT_DIR_SUFFIX,
    MODEL_DIR_SUFFIX,
    MODEL_EXTENSIONS,
    SYMBOL_DIR_SUFFIX,
    AssetIndex,
)
from app.library.refs import model_key
from app.library.snapshot import Snapshot


class AssetKind(StrEnum):
    SYMBOL = "symbol"
    FOOTPRINT = "footprint"
    MODEL = "model"

    @property
    def label(self) -> str:
        return "3D model" if self is AssetKind.MODEL else self.value


# Where each kind is kept: the directory a self-contained library groups it
# under, and the suffix of each library's directory.
_LAYOUT = {
    AssetKind.SYMBOL: ("symbols", SYMBOL_DIR_SUFFIX, ".kicad_sym"),
    AssetKind.FOOTPRINT: ("footprints", FOOTPRINT_DIR_SUFFIX, ".kicad_mod"),
    AssetKind.MODEL: ("3dmodels", MODEL_DIR_SUFFIX, ""),
}

MAX_FILES = 20
MAX_DRAWING_BYTES = 2 * 1024 * 1024
MAX_MODEL_BYTES = 20 * 1024 * 1024

# What a library may be called: it becomes a directory's name, and KiCad
# shows it before the colon of every reference.
_LIBRARY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
# What a symbol or footprint's name cannot hold, because it becomes a file's.
_NOT_IN_A_FILE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Where a footprint finds its model when the library's own footprints do not
# say: the variable KiCad 10 sets up for its 3D models.
DEFAULT_MODEL_VARIABLE = "${KICAD10_3DMODEL_DIR}"
_VARIABLE = re.compile(r"^(\$\{[^}]+\})/")


@dataclass(frozen=True)
class UploadedFile:
    name: str
    data: bytes


@dataclass
class Item:
    """One symbol, footprint or model an upload adds, or replaces."""

    kind: AssetKind
    name: str  # the symbol's or footprint's name; a model's file name
    source_file: str  # the uploaded file it came out of
    data: bytes  # what will be written
    path: str = ""  # where, from the top of the repository
    replaces: bool = False
    # What uses what it replaces: parts by IPN for a symbol or footprint,
    # footprints for a model. A replacement changes all of them.
    used_by: tuple[str, ...] = ()
    drawing: str | None = None  # as SVG, for a symbol or footprint
    # Already in the library exactly as uploaded: nothing to write.
    unchanged: bool = False
    # A symbol as read, for drawing it with what it derives from.
    tree: SExpr | None = field(default=None, repr=False, compare=False)

    def ref(self, library: str) -> str:
        """How the rest of the library names it."""
        if self.kind is AssetKind.MODEL:
            return f"{library}{MODEL_DIR_SUFFIX}/{self.name}"
        return f"{library}:{self.name}"


@dataclass
class Plan:
    """What an upload would do to the library, and what stands in its way."""

    library: str
    items: list[Item] = field(default_factory=list)
    # Reasons it cannot be added. One sentence each, all of them at once.
    problems: list[str] = field(default_factory=list)
    # What was done to the files on the way, or is worth knowing: said, not
    # refused.
    notes: list[str] = field(default_factory=list)
    # Library tables to write, from the top of the repository, with their new
    # text: a library this upload begins is added to them.
    tables: dict[str, str] = field(default_factory=dict)

    @property
    def replaces(self) -> bool:
        return any(item.replaces for item in self.items)


# -- where things go ------------------------------------------------------------


def asset_directory(root: Path, kind: AssetKind, library: str) -> Path:
    """The directory one library's assets of one kind are kept in.

    Laid out as the repository already is: under `symbols/` and the like if it
    groups by kind, at the top if it keeps its libraries there the way KiCad's
    own repositories do, and grouped if it has none yet.
    """
    group, suffix, _ = _LAYOUT[kind]
    at_top = not any(root.glob(f"{group}/*{suffix}")) and any(root.glob(f"*{suffix}"))
    return (root if at_top else root / group) / f"{library}{suffix}"


def _file_name(kind: AssetKind, name: str) -> str:
    return name + _LAYOUT[kind][2]


def own_index(snapshot: Snapshot) -> tuple[str, AssetIndex] | None:
    """The repository changes are written to, as last indexed: the one the
    parts are read from, or the first there is."""
    indexes = snapshot.catalog.indexes
    wanted = snapshot.parts_source if snapshot.parts_source in indexes else None
    source = wanted or next(iter(indexes), None)
    return (source, indexes[source]) if source else None


def _model_variable(index: AssetIndex | None) -> str:
    """The variable the library's footprints reach their models through."""
    if index is None:
        return DEFAULT_MODEL_VARIABLE
    found = Counter(
        match.group(1)
        for footprint in index.footprints.values()
        for reference in footprint.model_refs
        if (match := _VARIABLE.match(reference.replace("\\", "/")))
    )
    return found.most_common(1)[0][0] if found else DEFAULT_MODEL_VARIABLE


# -- what uses what -------------------------------------------------------------


def parts_using(snapshot: Snapshot, kind: AssetKind, ref: str) -> list[str]:
    """The parts that name a symbol or footprint, by IPN."""
    if kind is AssetKind.MODEL:
        return []
    attribute = "symbol" if kind is AssetKind.SYMBOL else "footprint"
    return sorted(
        part.ipn for part in snapshot.parts.parts.values() if getattr(part, attribute) == ref
    )


def footprints_using(snapshot: Snapshot, key: str) -> list[str]:
    """The footprints, in any source, that name a 3D model."""
    return sorted(
        f"{footprint.library}:{footprint.name}"
        for index in snapshot.catalog.indexes.values()
        for footprint in index.footprints.values()
        if any(model_key(reference) == key for reference in footprint.model_refs)
    )


def symbols_extending(index: AssetIndex, library: str, name: str) -> list[str]:
    """The symbols that derive from one, which would lose their body without it."""
    return sorted(
        f"{symbol.library}:{symbol.name}"
        for symbol in index.symbols.values()
        if symbol.library == library and symbol.extends == name
    )


# -- reading what was uploaded ------------------------------------------------


@dataclass
class _Read:
    """An uploaded file, read."""

    file: UploadedFile
    kind: AssetKind
    tree: SExpr | None = None  # a symbol library or a footprint


def _kind_of(file: UploadedFile, problems: list[str]) -> AssetKind | None:
    suffix = posixpath.splitext(file.name.lower())[1]
    if suffix == ".kicad_sym":
        return AssetKind.SYMBOL
    if suffix == ".kicad_mod":
        return AssetKind.FOOTPRINT
    if suffix in MODEL_EXTENSIONS:
        return AssetKind.MODEL
    if suffix in (".lib", ".dcm"):
        problems.append(
            f"{file.name} is a KiCad 5 symbol library. Open it in KiCad's symbol editor, "
            "save it as a .kicad_sym, and upload that."
        )
    elif suffix == ".zip":
        problems.append(f"{file.name} is an archive. Unpack it and upload the files in it.")
    else:
        problems.append(
            f"{file.name} is not a symbol (.kicad_sym), a footprint (.kicad_mod) "
            "or a 3D model (.step, .stp, .wrl)."
        )
    return None


def _text(file: UploadedFile, problems: list[str]) -> str | None:
    try:
        return file.data.decode("utf-8-sig")
    except UnicodeDecodeError:
        problems.append(f"{file.name} is not a text file, as KiCad writes them.")
        return None


def _read_file(file: UploadedFile, problems: list[str]) -> _Read | None:
    kind = _kind_of(file, problems)
    if kind is None:
        return None

    limit = MAX_MODEL_BYTES if kind is AssetKind.MODEL else MAX_DRAWING_BYTES
    if len(file.data) > limit:
        problems.append(f"{file.name} is larger than {limit // (1024 * 1024)} MB.")
        return None
    if not file.data:
        problems.append(f"{file.name} is empty.")
        return None

    if kind is AssetKind.MODEL:
        head = file.data[:512].lstrip()
        if file.name.lower().endswith(".wrl"):
            if not head.startswith(b"#VRML"):
                problems.append(f"{file.name} is not a VRML file.")
                return None
        elif not head.startswith(b"ISO-10303-21"):
            problems.append(f"{file.name} is not a STEP file.")
            return None
        return _Read(file, kind)

    text = _text(file, problems)
    if text is None:
        return None
    if kind is AssetKind.SYMBOL and text.lstrip().startswith("EESchema-LIBRARY"):
        problems.append(
            f"{file.name} is in KiCad 5's format. Open it in KiCad's symbol editor, "
            "save it, and upload what it saves."
        )
        return None
    try:
        tree = loads(text)
    except ParseError as exc:
        problems.append(f"{file.name} cannot be read: {exc}.")
        return None

    expected = "kicad_symbol_lib" if kind is AssetKind.SYMBOL else "footprint"
    if kind is AssetKind.FOOTPRINT and tree.head == "module":
        problems.append(
            f"{file.name} is in KiCad 5's format. Open it in KiCad's footprint editor, "
            "save it, and upload what it saves."
        )
        return None
    if tree.head != expected:
        problems.append(f"{file.name} is not a KiCad {kind.label} file.")
        return None
    return _Read(file, kind, tree)


def _bad_name(name: str) -> bool:
    return not name.strip() or bool(_NOT_IN_A_FILE_NAME.search(name)) or len(name) > 200


# -- the plan ---------------------------------------------------------------


def plan_upload(library: str, files: Sequence[UploadedFile], snapshot: Snapshot) -> Plan:
    """What `files` would add to `library`, and everything that stands in the way."""
    library = library.strip()
    plan = Plan(library=library)
    problems = plan.problems

    if not _LIBRARY_NAME.match(library) or library.endswith(
        (SYMBOL_DIR_SUFFIX, FOOTPRINT_DIR_SUFFIX, MODEL_DIR_SUFFIX)
    ):
        problems.append(
            "Name the library: letters, digits and _ . + - only, as it will be "
            "written before the colon (Passives:R_0603)."
        )
    if not files:
        problems.append("Choose the files to upload.")
    if len(files) > MAX_FILES:
        problems.append(f"Upload at most {MAX_FILES} files at once.")
        return plan

    read = [found for file in files if (found := _read_file(file, problems)) is not None]

    owner = own_index(snapshot)
    own_source, own = owner if owner else (None, None)

    symbols = _symbol_items(read, problems)
    models = [
        Item(AssetKind.MODEL, posixpath.basename(r.file.name), r.file.name, r.file.data)
        for r in read
        if r.kind is AssetKind.MODEL
    ]
    for model in models:
        if _bad_name(model.name):
            problems.append(f"{model.name}: a file's name cannot hold \\ / : * ? \" < > |.")
    footprints = _footprint_items(read, models, library, own, snapshot, plan)

    items = [*symbols, *footprints, *models]
    seen: Counter[tuple[AssetKind, str]] = Counter((item.kind, item.name) for item in items)
    for (kind, name), times in seen.items():
        if times > 1:
            problems.append(f"The {kind.label} {name} is uploaded {times} times.")

    if library and own is not None:
        _against_the_library(items, library, own_source or "", own, snapshot, plan)
    _draw_symbols(symbols, library, own, plan)

    # What the library already has exactly as uploaded is left as it is: a
    # vendor's download often repeats a footprint the library took last time.
    for item in items:
        if item.unchanged:
            plan.notes.append(
                f"The {item.kind.label} {item.ref(library)} is in the library exactly as "
                "uploaded, and is left as it is."
            )
    plan.items = [item for item in items if not item.unchanged]
    if items and not plan.items and not problems:
        problems.append("Everything uploaded is in the library already, exactly as it is.")
    if own is not None and not problems:
        _add_to_tables(plan, own)
    return plan


# The library tables a repository may carry, for whoever uses it checked out:
# KiCad's HTTP library finds the drawings through them.
_TABLES = {AssetKind.SYMBOL: "sym-lib-table", AssetKind.FOOTPRINT: "fp-lib-table"}


def _value(entry: SExpr, key: str) -> str | None:
    """`(name "Passives")` inside a library table's entry, as "Passives"."""
    found = entry.child(key)
    return found.arg(0) if found is not None else None


def _add_to_tables(plan: Plan, own: AssetIndex) -> None:
    """Add a library this upload begins to the repository's library tables.

    Only to tables the repository has, and written the way its other entries
    are: the address is theirs with the library's directory in place of
    theirs, so `${KICAD_LIBRARY_DIR}/symbols/Passives.kicad_symdir` gives
    `${KICAD_LIBRARY_DIR}/symbols/Regulators.kicad_symdir`. The file is
    changed by adding a line, so the rest of it stays as it was written.
    """
    for kind, name in _TABLES.items():
        if not any(item.kind is kind for item in plan.items):
            continue
        path = own.root / name
        try:
            text = path.read_text(encoding="utf-8")
            tree = loads(text)
        except (OSError, UnicodeDecodeError, ParseError):
            continue
        entries = [(_value(entry, "name"), _value(entry, "uri")) for entry in tree.children("lib")]
        if any(library == plan.library for library, _ in entries):
            continue

        prefix = "${KICAD_LIBRARY_DIR}/" if not entries else None
        for library, uri in entries:
            if not library or not uri:
                continue
            theirs = own.relative(asset_directory(own.root, kind, library))
            if uri.replace("\\", "/").endswith(theirs):
                prefix = uri[: len(uri) - len(theirs)]
                break
        if prefix is None:
            plan.notes.append(
                f"{name} lists its libraries in a way this app does not follow; add "
                f"{plan.library} to it by hand."
            )
            continue

        directory = own.relative(asset_directory(own.root, kind, plan.library))
        line = (
            f'  (lib (name "{plan.library}")(type "KiCad")(uri "{prefix}{directory}")'
            '(options "")(descr ""))'
        )
        closing = text.rstrip().rfind(")")
        plan.tables[name] = f"{text[:closing].rstrip()}\n{line}\n{text[closing:]}"
        plan.notes.append(f"{plan.library} is added to {name}.")


def _symbol_items(read: Sequence[_Read], problems: list[str]) -> list[Item]:
    """Each symbol in the uploaded symbol files, as a file of its own."""
    items: list[Item] = []
    for found in read:
        if found.kind is not AssetKind.SYMBOL or found.tree is None:
            continue
        definitions = list(found.tree.children("symbol"))
        if not definitions:
            problems.append(f"{found.file.name} holds no symbol.")
            continue
        # The lines that say which version of the format the symbols are in,
        # kept with each of them so KiCad reads it by the same rules.
        header = [
            item
            for item in found.tree.lists()
            if item.head in ("version", "generator", "generator_version")
        ] or [sexpr("version", atom(symbol_tree.FORMAT_VERSION))]
        for node in definitions:
            name = symbol_tree.symbol_name(node) or ""
            if _bad_name(name):
                problems.append(
                    f"{found.file.name}: the symbol {name!r} cannot be a file's name; "
                    'rename it in KiCad first (no \\ / : * ? " < > |).'
                )
                continue
            document = SExpr([Atom("kicad_symbol_lib"), *header, node])
            data = dumps(document).encode("utf-8")
            items.append(Item(AssetKind.SYMBOL, name, found.file.name, data, tree=node))
    return items


def _footprint_items(
    read: Sequence[_Read],
    models: Sequence[Item],
    library: str,
    own: AssetIndex | None,
    snapshot: Snapshot,
    plan: Plan,
) -> list[Item]:
    """Each uploaded footprint, pointed at the models uploaded with it."""
    trees = [r for r in read if r.kind is AssetKind.FOOTPRINT and r.tree is not None]
    variable = _model_variable(own)
    by_name = {model.name.casefold(): model for model in models}
    used: set[str] = set()
    items: list[Item] = []

    for found in trees:
        tree = found.tree
        assert tree is not None
        name = tree.arg(0) or posixpath.splitext(posixpath.basename(found.file.name))[0]
        if _bad_name(name):
            plan.problems.append(
                f"{found.file.name}: the footprint {name!r} cannot be a file's name; "
                'rename it in KiCad first (no \\ / : * ? " < > |).'
            )
            continue

        changed = False
        nodes = list(tree.children("model"))
        for node in nodes:
            reference = node.arg(0) or ""
            key = model_key(reference)
            model = by_name.get(posixpath.basename(key).casefold())
            if model is not None:
                pointed = f"{variable}/{library}{MODEL_DIR_SUFFIX}/{model.name}"
                if reference != pointed:
                    node.items[1] = pointed
                    changed = True
                used.add(model.name)
            elif snapshot.catalog.model(key) is None:
                plan.notes.append(
                    f"The footprint {name} names the 3D model {reference}, which the library "
                    "does not have and which was not uploaded with it; it will place without one."
                )

        if not nodes and len(trees) == 1 and len(models) == 1:
            model = models[0]
            pointed = f"{variable}/{library}{MODEL_DIR_SUFFIX}/{model.name}"
            tree.items.append(
                sexpr(
                    "model",
                    pointed,
                    sexpr("offset", sexpr("xyz", atom(0), atom(0), atom(0))),
                    sexpr("scale", sexpr("xyz", atom(1), atom(1), atom(1))),
                    sexpr("rotate", sexpr("xyz", atom(0), atom(0), atom(0))),
                )
            )
            changed = True
            used.add(model.name)
            plan.notes.append(
                f"The footprint {name} named no 3D model, so it is given {model.name} at its "
                "origin. Check in KiCad's footprint editor that the model sits where it should."
            )

        data = dumps(tree).encode("utf-8") if changed else found.file.data
        item = Item(AssetKind.FOOTPRINT, name, found.file.name, data)
        try:
            item.drawing = render_footprint(tree, f"{library}:{name}")
        except Exception:  # a drawing is a nicety; the file is what matters
            item.drawing = None
        items.append(item)

    if trees:
        for model in models:
            if model.name not in used:
                plan.notes.append(f"No footprint uploaded with it names the 3D model {model.name}.")
    return items


def _against_the_library(
    items: Sequence[Item],
    library: str,
    own_source: str,
    own: AssetIndex,
    snapshot: Snapshot,
    plan: Plan,
) -> None:
    """Where each item goes, what it replaces, and what it would collide with."""
    for item in items:
        directory = asset_directory(own.root, item.kind, library)
        item.path = own.relative(directory / _file_name(item.kind, item.name))
        if item.kind is AssetKind.MODEL:
            key = item.ref(library)
            existing_path = own.models[key].path if key in own.models else None
            elsewhere = [
                source
                for source, index in snapshot.catalog.indexes.items()
                if source != own_source and key in index.models
            ]
        else:
            catalog = own.symbols if item.kind is AssetKind.SYMBOL else own.footprints
            existing = catalog.get((library, item.name))
            existing_path = existing.path if existing else None
            elsewhere = [
                source
                for source, index in snapshot.catalog.indexes.items()
                if source != own_source
                and (library, item.name)
                in (index.symbols if item.kind is AssetKind.SYMBOL else index.footprints)
            ]

        if elsewhere:
            plan.problems.append(
                f"The {item.kind.label} {item.ref(library)} is already in {elsewhere[0]}, "
                "which this app does not write to. Upload it under another library's name."
            )
            continue

        if item.kind is AssetKind.MODEL and _kept_in_lfs(own.root, item.path or "", item.name):
            plan.problems.append(
                f"This library keeps 3D models like {item.name} in Git LFS, which this app "
                "does not write. Add the model with git, then upload the footprint alone."
            )
            continue

        if existing_path is not None:
            if _same_bytes(existing_path, item.data):
                item.unchanged = True
                continue
            item.replaces = True
            item.path = own.relative(existing_path)
            item.used_by = tuple(
                footprints_using(snapshot, item.ref(library))
                if item.kind is AssetKind.MODEL
                else parts_using(snapshot, item.kind, item.ref(library))
            )


def _kept_in_lfs(root: Path, path: str, name: str) -> bool:
    """Whether the repository's .gitattributes sends a file to Git LFS.

    Committed by an app without LFS, such a file would sit where every clone
    expects a pointer. Only the patterns at the top of the repository are
    read, which is where they are kept.
    """
    try:
        lines = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    for line in lines:
        words = line.split()
        if len(words) < 2 or words[0].startswith("#") or "filter=lfs" not in words[1:]:
            continue
        pattern = words[0]
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern.lstrip("/")):
            return True
    return False


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _draw_symbols(
    symbols: Sequence[Item], library: str, own: AssetIndex | None, plan: Plan
) -> None:
    """Draw each symbol, resolving what it derives from among those uploaded
    with it and those already in the library."""
    uploaded = {item.name: item.tree for item in symbols if item.tree is not None}

    def node_of(name: str) -> SExpr | None:
        if name in uploaded:
            return uploaded[name]
        existing = own.symbols.get((library, name)) if own else None
        if existing is None:
            return None
        try:
            return loads(existing.path.read_text(encoding="utf-8")).child("symbol")
        except (OSError, UnicodeDecodeError, ParseError):
            return None

    for item in symbols:
        if item.name not in uploaded:
            continue
        chain = [uploaded[item.name]]
        broken = False
        while (parent := symbol_tree.extends(chain[-1])) is not None:
            node = node_of(parent)
            if node is None or len(chain) > 20:
                plan.problems.append(
                    f"The symbol {item.name} derives from {parent}, which is not in "
                    f"{library} and was not uploaded with it."
                )
                broken = True
                break
            chain.append(node)
        if broken:
            continue
        try:
            flat = symbol_tree.flatten(list(reversed(chain)), item.name)
            item.drawing = render_symbol(flat, f"{library}:{item.name}")
        except Exception:  # a drawing is a nicety; the file is what matters
            item.drawing = None


# -- writing --------------------------------------------------------------------


def _summary(verb: str, names: Sequence[str]) -> str:
    shown = ", ".join(names[:3])
    more = len(names) - 3
    return f"{verb} {shown}" + (f" and {more} more" if more > 0 else "")


def upload_subject(plan: Plan) -> str:
    """The commit's subject: what was added and what was replaced."""
    added = [
        f"{item.kind.label} {item.ref(plan.library)}" for item in plan.items if not item.replaces
    ]
    replaced = [
        f"{item.kind.label} {item.ref(plan.library)}" for item in plan.items if item.replaces
    ]
    said = []
    if added:
        said.append(_summary("Add", added))
    if replaced:
        said.append(_summary("replace" if added else "Replace", replaced))
    return "; ".join(said)


def write_upload(root: Path, plan: Plan) -> str:
    """Put what was uploaded into a checkout. Returns the commit's subject."""
    for item in plan.items:
        target = root / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.data)
    for name, text in plan.tables.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return upload_subject(plan)


@dataclass(frozen=True)
class Removal:
    kind: AssetKind
    ref: str
    path: str  # from the top of the repository


def plan_removal(kind: AssetKind, ref: str, snapshot: Snapshot) -> tuple[Removal | None, list[str]]:
    """Whether an asset can be taken out of the library, and where its file is.

    Only one that nothing uses: a part naming a symbol that is gone cannot be
    placed, and a footprint without its model places without one.
    """
    owner = own_index(snapshot)
    if owner is None:
        return None, ["The library has not been read yet."]
    own_source, own = owner

    if kind is AssetKind.MODEL:
        model = own.models.get(ref)
        if model is None:
            return None, [f"{own_source} has no 3D model {ref}."]
        users = footprints_using(snapshot, ref)
        if users:
            return None, [f"The 3D model {ref} is used by {', '.join(users)}."]
        return Removal(kind, ref, own.relative(model.path)), []

    parsed = LibraryRef.parse(ref)
    catalog = own.symbols if kind is AssetKind.SYMBOL else own.footprints
    found = catalog.get((parsed.library, parsed.name)) if parsed else None
    if parsed is None or found is None:
        return None, [f"{own_source} has no {kind.label} {ref}."]

    problems = []
    users = parts_using(snapshot, kind, ref)
    if users:
        problems.append(f"The {kind.label} {ref} is used by {', '.join(users)}.")
    if kind is AssetKind.SYMBOL:
        derived = symbols_extending(own, parsed.library, parsed.name)
        if derived:
            problems.append(f"The symbols {', '.join(derived)} derive from {ref}.")
    if problems:
        return None, problems
    return Removal(kind, ref, own.relative(found.path)), []


def write_removal(root: Path, removal: Removal) -> str:
    target = root / removal.path
    if target.exists():
        target.unlink()
    return f"Remove {removal.kind.label} {removal.ref}"

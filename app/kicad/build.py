"""Build the payload KiCad receives when a part is placed.

A library symbol is a template. What gets placed is an IPN: the same drawing,
carrying the internal part number, its field values, and a link back to the
part page. So the symbol is rewritten rather than served as-is.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from app.kicad.catalog import Catalog
from app.kicad.library import Symbol
from app.kicad.parts import Part, PartsIndex
from app.kicad.sexpr import Atom, SExpr, loads
from app.kicad.writer import dumps, sexpr, set_property

# Fields the platform owns. Anything else in a part's `fields` is passed
# through untouched.
IPN_FIELD = "IPN"
DATASHEET_FIELD = "Datasheet"


class BuildError(Exception):
    """A part could not be turned into something placeable."""


@dataclass(frozen=True)
class SymbolPayload:
    library: str
    name: str
    text: str

    @property
    def filename(self) -> str:
        return f"{self.name}.kicad_sym"


def _load_symbol_node(symbol: Symbol) -> SExpr:
    """The (symbol ...) node from a symbol's own file."""
    try:
        tree = loads(Path(symbol.path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise BuildError(f"cannot read {symbol.path}: {exc}") from exc

    node = tree.child("symbol")
    if node is None:
        raise BuildError(f"{symbol.path} contains no symbol")
    return node


def _replace_property(target: SExpr, prop: SExpr) -> None:
    """Overwrite a property on `target` with `prop`, keeping the child's layout."""
    name = prop.atoms()[0] if prop.atoms() else None
    if name is None:
        return

    for i, item in enumerate(target.items):
        if isinstance(item, SExpr) and item.head == "property":
            existing = item.atoms()
            if existing and existing[0] == name:
                target.items[i] = copy.deepcopy(prop)
                return

    last = 1
    for i, item in enumerate(target.items):
        if isinstance(item, SExpr) and item.head == "property":
            last = i + 1
    target.items.insert(last, copy.deepcopy(prop))


def _split_unit_suffix(unit_name: str, symbol_name: str) -> tuple[int, int] | None:
    """Read the trailing _<unit>_<style> from a unit sub-symbol's name."""
    if not unit_name.startswith(symbol_name + "_"):
        return None
    parts = unit_name[len(symbol_name) + 1:].split("_")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _merge_common_units(symbol: SExpr, name: str) -> None:
    """Fold unit 0 into every real unit.

    Unit 0 means "common to all units", so distributing it changes nothing
    semantically. It is done because KiCad's remote-symbol receive path drops
    symbols that use it: every symbol that arrives intact from a working
    provider carries a single _1_1 unit holding both graphics and pins.
    """
    units: dict[tuple[int, int], SExpr] = {}
    for sub in list(symbol.children("symbol")):
        atoms = sub.atoms()
        parsed = _split_unit_suffix(atoms[0], name) if atoms else None
        if parsed is None:
            continue
        units[parsed] = sub
        symbol.items.remove(sub)

    if not units:
        return

    styles = {style for _, style in units}
    numbers = {unit for unit, _ in units if unit > 0}
    if not numbers:
        numbers = {1}  # graphics existed only as unit 0

    for style in sorted(styles):
        common = units.get((0, style))
        for number in sorted(numbers):
            body = [i for i in (common.items[2:] if common else [])]
            own = units.get((number, style))
            if own is not None:
                body.extend(own.items[2:])
            if not body:
                continue
            merged = SExpr(items=[Atom("symbol"), f"{name}_{number}_{style}", *body])
            symbol.items.append(merged)


def flatten(nodes: list[SExpr], name: str) -> SExpr:
    """Collapse an inheritance chain into one self-contained symbol.

    KiCad saves a single symbol per payload, and a saved symbol still carrying
    `(extends ...)` would point at a parent that is not in the destination
    library. So the graphics are inherited here instead: the ancestor's body is
    taken as the base, each descendant's properties are layered on top, and the
    unit sub-symbols are renamed to match.

    `nodes` runs ancestor first; `name` is the symbol being placed.
    """
    base = copy.deepcopy(nodes[0])
    original = base.atoms()[0] if base.atoms() else name

    for descendant in nodes[1:]:
        for prop in descendant.children("property"):
            _replace_property(base, prop)

    base.items[1] = name

    # Units are named <symbol>_<unit>_<style>; they must follow the rename or
    # KiCad treats them as belonging to a different symbol and draws nothing.
    for sub in base.children("symbol"):
        sub_atoms = sub.atoms()
        if sub_atoms and sub_atoms[0].startswith(original + "_"):
            sub.items[1] = name + sub_atoms[0][len(original):]

    base.items = [
        i for i in base.items
        if not (isinstance(i, SExpr) and i.head == "extends")
    ]

    # Keep embedded_fonts last, as KiCad writes it.
    trailing = [i for i in base.items
                if isinstance(i, SExpr) and i.head == "embedded_fonts"]
    for item in trailing:
        base.items.remove(item)

    _merge_common_units(base, name)
    base.items.extend(trailing)
    return base


def _header(version: str = "20251024") -> list:
    return [
        Atom("kicad_symbol_lib"),
        sexpr("version", Atom(version)),
        sexpr("generator", "kicad_library_manager"),
        sexpr("generator_version", "10.0"),
    ]


def build_symbol(
    part: Part,
    catalog: Catalog,
    parts_index: PartsIndex,
    public_url: str,
) -> SymbolPayload:
    """Render the placeable symbol for `part`.

    The result contains the part's symbol *and every ancestor it extends*.
    KiCad resolves `extends` within the library it is given, so a derived
    symbol sent alone would place a part with no pins and no body.
    """
    if not part.symbol or ":" not in part.symbol:
        raise BuildError(f"{part.ipn} names no usable symbol ({part.symbol!r})")

    library, name = part.symbol.split(":", 1)
    located = catalog.find_symbol(library, name)
    if not located:
        raise BuildError(f"{part.ipn} references symbol {part.symbol!r}, which no source provides")

    index = catalog.indexes[located[0].source_id]
    try:
        chain = index.resolve_chain(library, name)
    except ValueError as exc:  # extends cycle
        raise BuildError(str(exc)) from exc

    if not chain:
        raise BuildError(f"symbol {part.symbol!r} could not be resolved")

    missing_parent = chain[-1].extends is not None
    if missing_parent:
        raise BuildError(
            f"{part.ipn}: the extends chain for {part.symbol!r} is broken at "
            f"{chain[-1].name!r}, so the placed part would have no body"
        )

    # Ancestor first, so each descendant's overrides land on top.
    nodes = [_load_symbol_node(s) for s in reversed(chain)]
    placed = flatten(nodes, name)

    set_property(placed, IPN_FIELD, part.ipn)
    if part.description:
        set_property(placed, "Description", part.description)

    # The part page lists every approved source with its real datasheet, which
    # is more use than one manufacturer's PDF chosen arbitrarily.
    set_property(placed, DATASHEET_FIELD, f"{public_url.rstrip('/')}/ipn/{part.ipn}")

    for key, value in part.fields.items():
        set_property(placed, key, value)

    if part.footprint:
        set_property(placed, "Footprint", part.footprint)

    preferred = part.preferred_mpn
    if preferred:
        set_property(placed, "MPN", preferred)
        mpn = parts_index.mpns.get(preferred)
        if mpn and mpn.manufacturer:
            set_property(placed, "Manufacturer", mpn.manufacturer)

    tree = SExpr(items=[*_header(), placed])
    return SymbolPayload(library=library, name=name, text=dumps(tree))

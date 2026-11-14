"""Editing a parsed `(symbol ...)` tree.

Two jobs. Properties are read and rewritten, because what KiCad places is a
part rather than the library's template. And an inheritance chain is collapsed
into one self-contained symbol, because that is the only shape KiCad's remote
receive path accepts.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence

from app.kicad.sexpr import Atom, SExpr, atom, sexpr

# The format revision of the files this platform writes, as KiCad 10 stamps it.
FORMAT_VERSION = "20251024"
GENERATOR = "kicad_library_manager"
GENERATOR_VERSION = "10.0"

_FONT_SIZE_MM = 1.27


def symbol_name(node: SExpr) -> str | None:
    return node.arg(0)


def extends(node: SExpr) -> str | None:
    """The symbol this one derives from, if any."""
    parent = node.child("extends")
    return parent.arg(0) if parent else None


# -- properties ------------------------------------------------------------


def properties(node: SExpr) -> dict[str, str]:
    """Property values by name, in file order."""
    found: dict[str, str] = {}
    for prop in node.children("property"):
        name = prop.arg(0)
        if name is not None:
            found[name] = prop.arg(1, "") or ""
    return found


def _find_property(node: SExpr, name: str) -> tuple[int, SExpr] | None:
    for index, item in enumerate(node.items):
        if isinstance(item, SExpr) and item.head == "property" and item.arg(0) == name:
            return index, item
    return None


def _after_last_property(node: SExpr) -> int:
    """Where a new property goes: with the others, ahead of the graphics."""
    position = 2  # past the head and the name
    for index, item in enumerate(node.items):
        if isinstance(item, SExpr) and item.head == "property":
            position = index + 1
    return min(position, len(node.items))


def hidden_property(name: str, value: str) -> SExpr:
    """A property as KiCad writes one, kept off the schematic.

    Fields this platform adds belong in the BOM, not scattered across the
    sheet next to every resistor.
    """
    size = atom(_FONT_SIZE_MM)
    return SExpr(
        [
            Atom("property"),
            name,
            value,
            sexpr("at", atom(0), atom(0), atom(0)),
            sexpr("show_name", atom("no")),
            sexpr("do_not_autoplace", atom("no")),
            sexpr("hide", atom("yes")),
            sexpr("effects", sexpr("font", sexpr("size", size, size))),
        ]
    )


def set_property(node: SExpr, name: str, value: str) -> None:
    """Set a property's value, adding it hidden if the symbol has none.

    An existing property is edited in place so that its position, visibility
    and font survive. Rebuilding it from defaults would move the reference
    designator on every part placed.
    """
    found = _find_property(node, name)
    if found is None:
        node.items.insert(_after_last_property(node), hidden_property(name, value))
        return

    _, prop = found
    # items: head, name, value, then the layout lists.
    if len(prop.items) > 2 and isinstance(prop.items[2], str):
        prop.items[2] = value
    else:
        prop.items.insert(2, value)


def replace_property(node: SExpr, prop: SExpr) -> None:
    """Install a whole property node, layout included.

    This is what inheritance needs: a derived symbol's own property lands
    complete, rather than having its value transplanted into the ancestor's
    position and font.
    """
    name = prop.arg(0)
    if name is None:
        return

    replacement = copy.deepcopy(prop)
    found = _find_property(node, name)
    if found is None:
        node.items.insert(_after_last_property(node), replacement)
    else:
        node.items[found[0]] = replacement


# -- inheritance -----------------------------------------------------------


def _unit_suffix(unit_name: str, owner: str) -> tuple[int, int] | None:
    """The (unit, style) a sub-symbol's name ends in: `R_0603_1_1` -> (1, 1)."""
    if not unit_name.startswith(owner + "_"):
        return None
    parts = unit_name[len(owner) + 1 :].split("_")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _fold_common_unit(symbol: SExpr, name: str) -> None:
    """Distribute unit 0 across the real units.

    Unit 0 means "common to every unit", so copying it into each of them
    changes nothing semantically. It is done because KiCad's remote receive
    path drops symbols that use it: the part lands with no graphics at all.
    Every symbol that arrives intact carries its body and pins together in
    `_1_1`.
    """
    units: dict[tuple[int, int], SExpr] = {}
    for sub in list(symbol.children("symbol")):
        suffix = _unit_suffix(sub.arg(0) or "", name)
        if suffix is not None:
            units[suffix] = sub
            symbol.items.remove(sub)

    if not units:
        return

    styles = sorted({style for _, style in units})
    # A symbol drawn entirely in unit 0 still needs one real unit to live in.
    numbers = sorted({number for number, _ in units if number > 0}) or [1]

    for style in styles:
        common = units.get((0, style))
        for number in numbers:
            own = units.get((number, style))
            body = [*(common.items[2:] if common else []), *(own.items[2:] if own else [])]
            if body:
                symbol.items.append(SExpr([Atom("symbol"), f"{name}_{number}_{style}", *body]))


def flatten(chain: Sequence[SExpr], name: str) -> SExpr:
    """Collapse an inheritance chain into one self-contained symbol.

    KiCad saves exactly one symbol per payload, and a saved symbol still
    carrying `(extends ...)` points at a parent that is not in the library it
    was saved to. So the inheritance is resolved here: the root ancestor's
    body is the base, each descendant's properties are layered over it, and
    the result takes the name of the symbol being placed.

    `chain` runs from the root ancestor to the symbol itself. Nothing in it is
    modified.
    """
    flat = copy.deepcopy(chain[0])
    original = symbol_name(flat) or name

    for descendant in chain[1:]:
        for prop in descendant.children("property"):
            replace_property(flat, prop)

    if len(flat.items) > 1 and isinstance(flat.items[1], str):
        flat.items[1] = name
    else:
        flat.items.insert(1, name)

    # Units are named <symbol>_<unit>_<style>. They have to follow the rename,
    # or KiCad takes them for another symbol's and draws nothing.
    for sub in flat.children("symbol"):
        unit_name = sub.arg(0) or ""
        if unit_name.startswith(original + "_"):
            sub.items[1] = name + unit_name[len(original) :]

    # KiCad writes embedded_fonts after the units; keep it there.
    trailing = [item for item in flat.lists() if item.head == "embedded_fonts"]
    flat.items = [
        item
        for item in flat.items
        if not (isinstance(item, SExpr) and item.head in ("extends", "embedded_fonts"))
    ]

    _fold_common_unit(flat, name)
    flat.items.extend(trailing)
    return flat


def library_document(symbol: SExpr) -> SExpr:
    """Wrap one symbol as a complete `.kicad_sym` file."""
    return SExpr(
        [
            Atom("kicad_symbol_lib"),
            sexpr("version", atom(FORMAT_VERSION)),
            sexpr("generator", GENERATOR),
            sexpr("generator_version", GENERATOR_VERSION),
            symbol,
        ]
    )

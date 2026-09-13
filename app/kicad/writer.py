"""Write s-expressions back out in KiCad's own layout.

Needed because a symbol cannot be served verbatim: the placed part carries its
IPN, its field values and a link back to the part page, none of which exist in
the library file.
"""

from __future__ import annotations

import copy

from app.kicad.sexpr import Atom, SExpr

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


def quote(value: str) -> str:
    return '"' + "".join(_ESCAPES.get(ch, ch) for ch in value) + '"'


# KiCad packs coordinate lists onto shared lines, wrapping near this column.
# Cosmetic -- it parses either way -- but matching means a file we serve is
# byte-identical to one KiCad wrote itself.
_WRAP_COLUMN = 120
_TAB_WIDTH = 2


def _render_points(node: SExpr, indent: int, out: list[str]) -> None:
    pad = "	" * indent
    rendered = [
        "(" + " ".join(str(i) if isinstance(i, Atom) else quote(str(i)) for i in c.items) + ")"
        for c in node.items
        if isinstance(c, SExpr)
    ]

    line: list[str] = []
    width = indent * _TAB_WIDTH
    for item in rendered:
        extra = len(item) + (1 if line else 0)
        if line and width + extra > _WRAP_COLUMN:
            out.append(pad + " ".join(line))
            line, width = [item], indent * _TAB_WIDTH + len(item)
        else:
            line.append(item)
            width += extra
    if line:
        out.append(pad + " ".join(line))


def _render(node, indent: int, out: list[str]) -> None:
    pad = "\t" * indent

    if not isinstance(node, SExpr):
        out.append(pad + (str(node) if isinstance(node, Atom) else quote(str(node))))
        return

    parts = [
        str(i) if isinstance(i, Atom) else quote(str(i))
        for i in node.items
        if not isinstance(i, SExpr)
    ]
    children = [i for i in node.items if isinstance(i, SExpr)]

    head = pad + "(" + " ".join(parts)

    # KiCad keeps leaf lists on one line -- (at 0 0 90) -- and breaks only when
    # a list has nested lists of its own.
    if not children:
        out.append(head + ")")
        return

    out.append(head)
    if node.head == "pts" and all(
        isinstance(c, SExpr) and not any(isinstance(g, SExpr) for g in c.items) for c in children
    ):
        _render_points(node, indent + 1, out)
    else:
        for child in children:
            _render(child, indent + 1, out)
    out.append(pad + ")")


def dumps(node: SExpr) -> str:
    """Render `node` as KiCad writes it: tabs, leaf lists inline."""
    out: list[str] = []
    _render(node, 0, out)
    return "\n".join(out) + "\n"


# -- construction helpers ------------------------------------------------


def atom(value) -> Atom:
    return Atom(str(value))


def sexpr(head: str, *items) -> SExpr:
    return SExpr(items=[Atom(head), *items])


def effects(size: float = 1.27) -> SExpr:
    return sexpr("effects", sexpr("font", sexpr("size", atom(size), atom(size))))


def make_property(
    name: str,
    value: str,
    at: tuple[float, float, float] = (0, 0, 0),
    hide: bool = True,
) -> SExpr:
    items = [
        Atom("property"),
        name,
        value,
        sexpr("at", *(atom(v) for v in at)),
        sexpr("show_name", atom("no")),
        sexpr("do_not_autoplace", atom("no")),
    ]
    if hide:
        items.append(sexpr("hide", atom("yes")))
    items.append(effects())
    return SExpr(items=items)


def property_name(node: SExpr) -> str | None:
    atoms = node.atoms()
    return atoms[0] if atoms else None


def insert_index(symbol: SExpr) -> int:
    """After the last existing property, before the graphics sub-symbols."""
    last = 1
    for i, item in enumerate(symbol.items):
        if isinstance(item, SExpr) and item.head == "property":
            last = i + 1
    return last


def find_property(symbol: SExpr, name: str) -> tuple[int, SExpr] | None:
    """A property by name, with its position among the symbol's items."""
    for i, item in enumerate(symbol.items):
        if isinstance(item, SExpr) and item.head == "property" and property_name(item) == name:
            return i, item
    return None


def set_property(symbol: SExpr, name: str, value: str) -> None:
    """Overwrite a property's value, or append it if absent.

    An existing property is edited in place so its position, visibility and
    font survive; a replacement built from defaults would move the reference
    designator on every placed part.
    """
    found = find_property(symbol, name)
    if found is not None:
        _, prop = found
        if len(prop.items) >= 3:
            prop.items[2] = value
        else:
            prop.items.append(value)
        return

    # Fields invented by the platform are hidden: they belong in the BOM, not
    # scattered across the schematic.
    symbol.items.insert(insert_index(symbol), make_property(name, value))


def replace_property(symbol: SExpr, prop: SExpr) -> None:
    """Overwrite a property with a whole node, keeping that node's layout.

    Unlike `set_property` this carries the replacement's position, visibility
    and font across, which is what an inherited symbol needs: a derived
    symbol's own property nodes should land complete, not have their values
    transplanted into the ancestor's.
    """
    name = property_name(prop)
    if name is None:
        return

    found = find_property(symbol, name)
    index = found[0] if found is not None else insert_index(symbol)
    replacement = copy.deepcopy(prop)

    if found is not None:
        symbol.items[index] = replacement
    else:
        symbol.items.insert(index, replacement)

"""KiCad's s-expression files, read and written.

Deliberately not a model of the format. Everything this platform does to a
file -- enumerate symbols, read properties, rewrite a few of them, draw the
rest -- works on a generic tree, so a token added by a newer KiCad is just
another node. A full object model has to know every token, and a file
containing one it does not know fails to load.

Writing reproduces KiCad's own layout, so that a file served from here is
indistinguishable from one KiCad saved itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field


class Atom(str):
    """A bare, unquoted token: a number, a keyword, yes/no.

    Kept apart from a quoted string so a parsed file can be written back out.
    `(at 0 0 90)` and `(property "Value" "10k")` are different, and collapsing
    both to str would quote the numbers and produce a file KiCad rejects.
    """

    __slots__ = ()


class ParseError(ValueError):
    """The text is not one well-formed s-expression."""


@dataclass(eq=False)
class SExpr:
    """One parenthesised list."""

    items: list[SExpr | str] = field(default_factory=list)

    @property
    def head(self) -> str | None:
        """The leading token, which names what kind of list this is."""
        first = self.items[0] if self.items else None
        return first if isinstance(first, str) else None

    @property
    def args(self) -> list[str]:
        """The strings and atoms directly inside this list, after the head."""
        return [item for item in self.items[1:] if isinstance(item, str)]

    def arg(self, index: int = 0, default: str | None = None) -> str | None:
        args = self.args
        return args[index] if index < len(args) else default

    def children(self, head: str) -> Iterator[SExpr]:
        """Direct child lists with this head."""
        for item in self.items:
            if isinstance(item, SExpr) and item.head == head:
                yield item

    def child(self, head: str) -> SExpr | None:
        return next(self.children(head), None)

    def lists(self) -> Iterator[SExpr]:
        """Every direct child list, whatever its head."""
        for item in self.items:
            if isinstance(item, SExpr):
                yield item

    def descendants(self, head: str) -> Iterator[SExpr]:
        """Lists with this head at any depth, in document order."""
        for item in self.lists():
            if item.head == head:
                yield item
            yield from item.descendants(head)


# -- reading ---------------------------------------------------------------

_TOKEN = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<open>\()
    | (?P<close>\))
    | "(?P<string>(?:[^"\\]|\\.)*)"
    | (?P<atom>[^\s()"]+)
    """,
    re.VERBOSE | re.DOTALL,
)
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_UNESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


def _unescape(raw: str) -> str:
    if "\\" not in raw:
        return raw
    return _ESCAPE.sub(lambda m: _UNESCAPES.get(m.group(1), m.group(1)), raw)


def loads(text: str) -> SExpr:
    """Parse the single top-level expression in `text`."""
    stack: list[SExpr] = []
    root: SExpr | None = None
    position, end = 0, len(text)

    while position < end:
        match = _TOKEN.match(text, position)
        if match is None:
            # The only thing the pattern cannot consume is a quote that never
            # closes.
            raise ParseError(f"unterminated string at offset {position}")
        position = match.end()
        kind = match.lastgroup

        if kind == "space":
            continue

        if kind == "open":
            node = SExpr()
            if stack:
                stack[-1].items.append(node)
            stack.append(node)
            continue

        if kind == "close":
            if not stack:
                raise ParseError("unbalanced closing parenthesis")
            node = stack.pop()
            if not stack:
                if root is not None:
                    raise ParseError("more than one top-level expression")
                root = node
            continue

        if not stack:
            raise ParseError(f"token {match.group()!r} outside any expression")
        if kind == "string":
            stack[-1].items.append(_unescape(match.group("string")))
        else:
            stack[-1].items.append(Atom(match.group("atom")))

    if stack:
        raise ParseError("unbalanced opening parenthesis")
    if root is None:
        raise ParseError("no expression found")
    return root


# -- writing ---------------------------------------------------------------

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}

# KiCad packs coordinate lists onto shared lines and wraps them near this
# column, counting a tab as two. Cosmetic -- the file parses either way -- but
# matching it is what makes a served file byte-identical to KiCad's own.
_WRAP_COLUMN = 120
_TAB_WIDTH = 2


def quote(value: str) -> str:
    return '"' + "".join(_ESCAPES.get(ch, ch) for ch in value) + '"'


def _token(value: str) -> str:
    return str(value) if isinstance(value, Atom) else quote(value)


def _is_leaf(node: SExpr | str) -> bool:
    return isinstance(node, SExpr) and not any(isinstance(i, SExpr) for i in node.items)


def _inline(node: SExpr) -> str:
    return "(" + " ".join(_token(item) for item in node.items) + ")"


def _write_points(points: list[SExpr], indent: int, out: list[str]) -> None:
    """Coordinates share lines, breaking before the one that would overflow."""
    pad = "\t" * indent
    line: list[str] = []
    width = indent * _TAB_WIDTH

    for point in points:
        text = _inline(point)
        extra = len(text) + (1 if line else 0)
        if line and width + extra > _WRAP_COLUMN:
            out.append(pad + " ".join(line))
            line, width = [text], indent * _TAB_WIDTH + len(text)
        else:
            line.append(text)
            width += extra

    if line:
        out.append(pad + " ".join(line))


def _write(node: SExpr, indent: int, out: list[str]) -> None:
    pad = "\t" * indent

    # A list holding no lists of its own stays on one line: (at 0 0 90).
    if _is_leaf(node):
        out.append(pad + _inline(node))
        return

    # The tokens before the first nested list ride on the opening line; from
    # there on everything gets a line of its own, in the order it was read.
    split = next(i for i, item in enumerate(node.items) if isinstance(item, SExpr))
    out.append(pad + "(" + " ".join(_token(item) for item in node.items[:split]))

    rest = node.items[split:]
    if node.head == "pts" and all(_is_leaf(item) for item in rest):
        _write_points(rest, indent + 1, out)
    else:
        for item in rest:
            if isinstance(item, SExpr):
                _write(item, indent + 1, out)
            else:
                out.append(pad + "\t" + _token(item))

    out.append(pad + ")")


def dumps(node: SExpr) -> str:
    """Render `node` the way KiCad writes it: tabs, leaf lists inline."""
    out: list[str] = []
    _write(node, 0, out)
    return "\n".join(out) + "\n"


# -- building --------------------------------------------------------------


def atom(value: object) -> Atom:
    return Atom(str(value))


def sexpr(head: str, *items: SExpr | str) -> SExpr:
    """A list whose head is the bare token `head`."""
    return SExpr([Atom(head), *items])

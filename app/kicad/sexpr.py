"""A tolerant reader for KiCad's s-expression files.

Deliberately not a model of the format. We only ever need to enumerate symbols,
read their properties and slice a subtree back out; everything else passes
through untouched. That is what keeps this working across KiCad releases -- a
full object model has to know every token, so a file containing a newly added
one fails to load, while an unknown token here is just another node.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Union

Node = Union["SExpr", str]


class ParseError(Exception):
    """Raised when a file is not well-formed s-expression."""


@dataclass
class SExpr:
    """One parenthesised list. `head` is its first atom, if any."""

    items: list[Node]

    @property
    def head(self) -> str | None:
        first = self.items[0] if self.items else None
        return first if isinstance(first, str) else None

    def children(self, head: str) -> Iterator["SExpr"]:
        """Direct child lists whose head matches."""
        for item in self.items:
            if isinstance(item, SExpr) and item.head == head:
                yield item

    def child(self, head: str) -> "SExpr | None":
        return next(self.children(head), None)

    def atoms(self) -> list[str]:
        """Bare atoms of this list, excluding the head."""
        return [i for i in self.items[1:] if isinstance(i, str)]

    def descendants(self, head: str) -> Iterator["SExpr"]:
        for item in self.items:
            if isinstance(item, SExpr):
                if item.head == head:
                    yield item
                yield from item.descendants(head)


def _tokenize(text: str) -> Iterator[tuple[str, str]]:
    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch in " \t\r\n":
            i += 1
            continue

        if ch in "()":
            yield ("paren", ch)
            i += 1
            continue

        if ch == '"':
            i += 1
            out: list[str] = []
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                out.append(c)
                i += 1
            else:
                raise ParseError("unterminated string")
            yield ("str", "".join(out))
            continue

        start = i
        while i < n and text[i] not in ' \t\r\n()"':
            i += 1
        if i == start:  # a lone quote in an odd position
            raise ParseError(f"unexpected character {text[i]!r} at offset {i}")
        yield ("atom", text[start:i])


def loads(text: str) -> SExpr:
    """Parse the single top-level expression in `text`."""
    stack: list[SExpr] = []
    root: SExpr | None = None

    for kind, value in _tokenize(text):
        if kind == "paren" and value == "(":
            node = SExpr(items=[])
            if stack:
                stack[-1].items.append(node)
            stack.append(node)
            continue

        if kind == "paren" and value == ")":
            if not stack:
                raise ParseError("unbalanced closing parenthesis")
            node = stack.pop()
            if not stack:
                if root is not None:
                    raise ParseError("more than one top-level expression")
                root = node
            continue

        if not stack:
            raise ParseError(f"atom {value!r} outside any expression")
        stack[-1].items.append(value)

    if stack:
        raise ParseError("unbalanced opening parenthesis")
    if root is None:
        raise ParseError("no expression found")
    return root

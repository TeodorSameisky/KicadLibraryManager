"""Primitives shared by the symbol and footprint renderers.

Both walk a parsed file, accumulate a bounding box and emit SVG, so the parts
that are genuinely common live here rather than one renderer reaching into the
other's private helpers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.kicad.sexpr import SExpr

_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def escape(text: str) -> str:
    """Escape text for inclusion in markup.

    Applied to anything drawn from library files: descriptions, pad numbers and
    part numbers all originate in a repository rather than from us.
    """
    return "".join(_XML_ESCAPES.get(ch, ch) for ch in str(text))


def floats(node: SExpr | None) -> list[float]:
    """Numeric atoms of a node, skipping anything that is not a number."""
    if node is None:
        return []
    out: list[float] = []
    for atom in node.atoms():
        try:
            out.append(float(atom))
        except ValueError:
            continue
    return out


def point(node: SExpr | None) -> tuple[float, float] | None:
    values = floats(node)
    return (values[0], values[1]) if len(values) >= 2 else None


def circle_through(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> tuple[float, float, float] | None:
    """Centre and radius of the circle through three points, or None.

    KiCad stores an arc as three points on it, while SVG wants a radius. None
    means the points are collinear, which has no circle and should be drawn as
    a straight line rather than silently dropped.
    """
    (x1, y1), (x2, y2), (x3, y3) = a, b, c
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None

    ux = (
        (x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)
    ) / d
    uy = (
        (x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)
    ) / d
    return ux, uy, math.hypot(x1 - ux, y1 - uy)


def arc_sweep(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> int:
    """SVG sweep flag for an arc running a -> b -> c."""
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return 1 if cross > 0 else 0


@dataclass
class Bounds:
    """The extent of what has been drawn, in millimetres."""

    min_x: float = math.inf
    min_y: float = math.inf
    max_x: float = -math.inf
    max_y: float = -math.inf

    def add(self, x: float, y: float) -> None:
        self.min_x, self.min_y = min(self.min_x, x), min(self.min_y, y)
        self.max_x, self.max_y = max(self.max_x, x), max(self.max_y, y)

    def add_point(self, p: tuple[float, float]) -> None:
        self.add(p[0], p[1])

    @property
    def empty(self) -> bool:
        return self.min_x > self.max_x

    def viewbox(self, margin: float) -> tuple[float, float, float, float]:
        return (
            self.min_x - margin,
            self.min_y - margin,
            (self.max_x - self.min_x) + 2 * margin,
            (self.max_y - self.min_y) + 2 * margin,
        )


@dataclass
class Canvas:
    """Accumulates markup and the extent of what has been drawn."""

    parts: list[str] = field(default_factory=list)
    bounds: Bounds = field(default_factory=Bounds)

    def emit(self, markup: str) -> None:
        self.parts.append(markup)

    def markup(self) -> str:
        return "".join(self.parts)


def document(
    *,
    css_class: str,
    view: tuple[float, float, float, float],
    px_per_mm: float,
    title: str,
    style: str,
    body: str,
) -> str:
    min_x, min_y, width, height = view
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{min_x:.4f} {min_y:.4f} {width:.4f} {height:.4f}" '
        f'width="{width * px_per_mm:.0f}" height="{height * px_per_mm:.0f}" '
        f'class="{css_class}" role="img" aria-label="{escape(title or css_class)}">'
        f"<title>{escape(title)}</title>{style}{body}</svg>"
    )


def placeholder(css_class: str, title: str, style: str, message: str) -> str:
    """Stands in for a file with nothing drawable, rather than an empty box."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20" '
        f'width="160" height="80" class="{css_class}" role="img" '
        f'aria-label="{escape(title)}: {escape(message)}">'
        f"<title>{escape(title)}</title>{style}"
        f'<text x="20" y="11" text-anchor="middle" font-size="4" '
        f'fill="currentColor" opacity="0.5">{escape(message)}</text></svg>'
    )

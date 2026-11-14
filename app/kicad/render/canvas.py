"""What the symbol and footprint renderers share.

Both walk a parsed file, grow a bounding box as they go and emit SVG, so the
geometry and the document scaffolding live here rather than one renderer
borrowing the other's private helpers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.kicad.sexpr import SExpr

Point = tuple[float, float]

_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def escape(text: object) -> str:
    """Escape text for markup.

    Applied to everything drawn from a library file. Descriptions, pin numbers
    and part numbers all come out of a repository, not from this code.
    """
    return "".join(_XML_ESCAPES.get(ch, ch) for ch in str(text))


def fmt(value: float) -> str:
    """A coordinate, at a precision finer than KiCad's own grid."""
    return f"{value:.4f}"


# -- reading geometry ------------------------------------------------------


def numbers(node: SExpr | None) -> list[float]:
    """The numeric arguments of a list, skipping anything that is not one."""
    if node is None:
        return []
    found: list[float] = []
    for value in node.args:
        try:
            found.append(float(value))
        except ValueError:
            continue
    return found


def point(node: SExpr | None) -> Point | None:
    values = numbers(node)
    return (values[0], values[1]) if len(values) >= 2 else None


def points(node: SExpr | None) -> list[Point]:
    """The coordinates of a `(pts (xy ..) (xy ..))` list."""
    if node is None:
        return []
    return [p for p in (point(xy) for xy in node.children("xy")) if p is not None]


def stroke_width(node: SExpr, default: float) -> float:
    """A shape's line width. Zero means "the default", not "invisible"."""
    stroke = node.child("stroke")
    width = numbers(stroke.child("width")) if stroke else []
    return width[0] if width and width[0] > 0 else default


# -- arcs ------------------------------------------------------------------


@dataclass(frozen=True)
class Arc:
    """An SVG arc segment equivalent to KiCad's three points on a curve."""

    radius: float
    large: int  # SVG's large-arc flag
    sweep: int  # SVG's sweep flag, in the coordinates the points were given in


def _cross(origin: Point, a: Point, b: Point) -> float:
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def arc_through(start: Point, mid: Point, end: Point) -> Arc | None:
    """The arc from `start` to `end` passing through `mid`, or None.

    KiCad stores an arc as three points on it; SVG wants a radius and two
    flags. None means the points are collinear: there is no circle through
    them, and the caller should draw the chord rather than drop the shape.
    """
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None

    s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
    centre = (
        (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d,
        (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d,
    )

    turn = _cross(start, mid, end)
    return Arc(
        radius=math.hypot(x1 - centre[0], y1 - centre[1]),
        # More than a half turn exactly when the centre sits on the same side
        # of the chord as the arc's own midpoint.
        large=int(turn * _cross(start, centre, end) > 0),
        # SVG sweeps towards increasing angle, which is the direction of a
        # positive turn through the three points.
        sweep=int(turn > 0),
    )


# -- accumulating a drawing ------------------------------------------------


class Bounds:
    """The extent of what has been drawn, in millimetres."""

    def __init__(self) -> None:
        self.min_x = self.min_y = math.inf
        self.max_x = self.max_y = -math.inf

    def include(self, x: float, y: float) -> None:
        self.min_x, self.min_y = min(self.min_x, x), min(self.min_y, y)
        self.max_x, self.max_y = max(self.max_x, x), max(self.max_y, y)

    def include_all(self, pts: list[Point] | tuple[Point, ...]) -> None:
        for x, y in pts:
            self.include(x, y)

    @property
    def empty(self) -> bool:
        return self.min_x > self.max_x

    @property
    def centre_y(self) -> float:
        return (self.min_y + self.max_y) / 2

    def view_box(self, margin: float) -> tuple[float, float, float, float]:
        return (
            self.min_x - margin,
            self.min_y - margin,
            (self.max_x - self.min_x) + 2 * margin,
            (self.max_y - self.min_y) + 2 * margin,
        )


# -- the finished document -------------------------------------------------


def document(
    *,
    css_class: str,
    view_box: tuple[float, float, float, float],
    px_per_mm: float,
    title: str,
    style: str,
    body: str,
) -> str:
    x, y, width, height = view_box
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{fmt(x)} {fmt(y)} {fmt(width)} {fmt(height)}" '
        f'width="{width * px_per_mm:.0f}" height="{height * px_per_mm:.0f}" '
        f'class="{css_class}" role="img" aria-label="{escape(title or css_class)}">'
        f"<title>{escape(title)}</title>{style}{body}</svg>"
    )


def placeholder(*, css_class: str, title: str, style: str, message: str) -> str:
    """Stands in for a file with nothing drawable, rather than an empty box."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20" '
        f'width="160" height="80" class="{css_class}" role="img" '
        f'aria-label="{escape(title)}: {escape(message)}">'
        f"<title>{escape(title)}</title>{style}"
        '<text x="20" y="11" text-anchor="middle" font-size="4" '
        f'fill="currentColor" opacity="0.5">{escape(message)}</text></svg>'
    )

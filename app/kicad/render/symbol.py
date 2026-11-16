"""Draw a KiCad symbol as SVG.

Rendered from the parsed symbol rather than by shelling out to KiCad, which
would mean installing the whole application in the container to draw a
resistor.

A symbol's Y axis runs upwards and SVG's runs downwards. The shapes are
emitted exactly as the file gives them and the finished drawing is flipped
once, rather than every coordinate being negated on its way through.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from app.kicad.render.canvas import (
    Bounds,
    arc_through,
    document,
    escape,
    fmt,
    numbers,
    placeholder,
    point,
    points,
    stroke_width,
)
from app.kicad.sexpr import SExpr

CSS_CLASS = "ksym"
PX_PER_MM = 12.0
MARGIN_MM = 1.5
DEFAULT_STROKE_MM = 0.1524  # KiCad's own default symbol line width
PIN_DOT_RADIUS_MM = 0.3

# The palette travels inside the drawing because an <img>-embedded SVG is its
# own document and inherits nothing from the page around it. It is scoped to
# the drawing's class rather than :root so that inlining the same markup into
# a page does not redefine that page's variables.
STYLE = (
    "<style>"
    ".ksym{--symbol-line:#14171c;--symbol-fill:#fffbe6;--symbol-pin:#8a94a6;"
    "--symbol-hl:#2f6fd0}"
    "@media(prefers-color-scheme:dark){"
    ".ksym{--symbol-line:#e8eaed;--symbol-fill:#2a2410;--symbol-pin:#6b7480;"
    "--symbol-hl:#6fa4f0}}"
    ".ksym .pin line,.ksym .pin circle{transition:stroke .1s,fill .1s}"
    ".ksym .pin.hl line{stroke:var(--symbol-hl);stroke-width:.28}"
    ".ksym .pin.hl circle{stroke:var(--symbol-hl);fill:var(--symbol-hl)}"
    "</style>"
)

_FILLS = {"outline": "var(--symbol-line)", "background": "var(--symbol-fill)"}


class _Drawing:
    def __init__(self) -> None:
        self.markup: list[str] = []
        self.bounds = Bounds()


def _paint(node: SExpr) -> str:
    fill = node.child("fill")
    kind = fill.child("type") if fill else None
    return (
        f'fill="{_FILLS.get((kind.arg(0) if kind else None) or "", "none")}" '
        f'stroke="var(--symbol-line)" '
        f'stroke-width="{fmt(stroke_width(node, DEFAULT_STROKE_MM))}" '
        'stroke-linecap="round" stroke-linejoin="round"'
    )


def _rectangle(node: SExpr, drawing: _Drawing) -> None:
    start, end = point(node.child("start")), point(node.child("end"))
    if start is None or end is None:
        return
    x, y = min(start[0], end[0]), min(start[1], end[1])
    width, height = abs(end[0] - start[0]), abs(end[1] - start[1])
    drawing.bounds.include_all([(x, y), (x + width, y + height)])
    drawing.markup.append(
        f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(width)}" height="{fmt(height)}" '
        f"{_paint(node)}/>"
    )


def _polyline(node: SExpr, drawing: _Drawing) -> None:
    pts = points(node.child("pts"))
    if len(pts) < 2:
        return
    drawing.bounds.include_all(pts)
    path = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)
    drawing.markup.append(f'<polyline points="{path}" {_paint(node)}/>')


def _circle(node: SExpr, drawing: _Drawing) -> None:
    centre = point(node.child("center"))
    radius = numbers(node.child("radius"))
    if centre is None or not radius:
        return
    (cx, cy), r = centre, radius[0]
    drawing.bounds.include_all([(cx - r, cy - r), (cx + r, cy + r)])
    drawing.markup.append(f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{fmt(r)}" {_paint(node)}/>')


def _arc(node: SExpr, drawing: _Drawing) -> None:
    start, mid, end = (point(node.child(key)) for key in ("start", "mid", "end"))
    if start is None or mid is None or end is None:
        return
    drawing.bounds.include_all([start, mid, end])

    arc = arc_through(start, mid, end)
    if arc is None:  # collinear: there is no circle, so draw the chord
        drawing.markup.append(
            f'<polyline points="{fmt(start[0])},{fmt(start[1])} '
            f'{fmt(end[0])},{fmt(end[1])}" {_paint(node)}/>'
        )
        return

    drawing.markup.append(
        f'<path d="M {fmt(start[0])},{fmt(start[1])} '
        f"A {fmt(arc.radius)},{fmt(arc.radius)} 0 {arc.large} {arc.sweep} "
        f'{fmt(end[0])},{fmt(end[1])}" {_paint(node)}/>'
    )


def _pin(node: SExpr, drawing: _Drawing) -> None:
    at = numbers(node.child("at"))
    length = numbers(node.child("length"))
    if len(at) < 3 or not length:
        return

    # (x, y) is where a wire attaches; the angle is the direction the pin
    # runs from there, towards the body.
    x, y, angle = at[0], at[1], math.radians(at[2])
    end_x, end_y = x + length[0] * math.cos(angle), y + length[0] * math.sin(angle)
    drawing.bounds.include_all([(x, y), (end_x, end_y)])

    # Grouped and tagged with its number so the page can pick out the pad it
    # maps to in the footprint drawn beside it.
    number = node.child("number")
    label = number.arg(0, "") if number else ""
    tag = f' data-pin="{escape(label)}"' if label else ""
    drawing.markup.append(
        f'<g class="pin"{tag}>'
        f'<line x1="{fmt(x)}" y1="{fmt(y)}" x2="{fmt(end_x)}" y2="{fmt(end_y)}" '
        f'stroke="var(--symbol-line)" stroke-width="{fmt(DEFAULT_STROKE_MM)}" '
        'stroke-linecap="round"/>'
        f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(PIN_DOT_RADIUS_MM)}" '
        'fill="none" stroke="var(--symbol-pin)" stroke-width="0.06"/>'
        "</g>"
    )


_SHAPES: dict[str, Callable[[SExpr, _Drawing], None]] = {
    "rectangle": _rectangle,
    "polyline": _polyline,
    "circle": _circle,
    "arc": _arc,
    "pin": _pin,
}


def _draw(node: SExpr, drawing: _Drawing) -> None:
    for item in node.lists():
        shape = _SHAPES.get(item.head or "")
        if shape is not None:
            shape(item, drawing)
        elif item.head == "symbol":  # a unit; its shapes are the symbol's
            _draw(item, drawing)


def render_symbol(symbol: SExpr, title: str = "") -> str:
    """Render one `(symbol ...)` node as a standalone SVG document."""
    drawing = _Drawing()
    _draw(symbol, drawing)

    if drawing.bounds.empty:
        return placeholder(css_class=CSS_CLASS, title=title, style=STYLE, message="no graphics")

    # Mirrored about the drawing's own horizontal centre line, so the view box
    # computed from the unflipped coordinates still frames it.
    flip = 2 * drawing.bounds.centre_y
    return document(
        css_class=CSS_CLASS,
        view_box=drawing.bounds.view_box(MARGIN_MM),
        px_per_mm=PX_PER_MM,
        title=title,
        style=STYLE,
        body=f'<g transform="translate(0 {fmt(flip)}) scale(1 -1)">{"".join(drawing.markup)}</g>',
    )

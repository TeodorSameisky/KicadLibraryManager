"""Draw a KiCad symbol as SVG.

Rendered from the parsed symbol rather than by shelling out to KiCad, which
would mean installing the whole application in the container to draw a
resistor.

KiCad's symbol coordinates run with Y upwards and SVG's run downwards, so the
finished drawing is flipped once rather than every coordinate being negated.
"""

from __future__ import annotations

import math

from app.kicad.sexpr import SExpr
from app.kicad.svg import (
    Canvas,
    arc_sweep,
    circle_through,
    document,
    escape,
    floats,
    placeholder,
    point,
)

PX_PER_MM = 12.0
MARGIN_MM = 1.5
DEFAULT_STROKE_MM = 0.1524   # KiCad's own default symbol line width
PIN_RADIUS_MM = 0.3
CSS_CLASS = "ksym"

# Scoped to the drawing's own class rather than :root, so inlining it into a
# page does not redefine that page's variables. The palette travels inside the
# file because an <img>-embedded SVG inherits nothing from its host.
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


def _stroke_width(node: SExpr) -> float:
    stroke = node.child("stroke")
    width = floats(stroke.child("width")) if stroke else []
    # Zero means "use the default", not "invisible".
    return width[0] if width and width[0] > 0 else DEFAULT_STROKE_MM


def _fill(node: SExpr) -> str:
    fill = node.child("fill")
    kind = fill.child("type").atoms()[0] if fill and fill.child("type") else "none"
    if kind == "outline":
        return "var(--symbol-line)"
    if kind == "background":
        return "var(--symbol-fill)"
    return "none"


def _attrs(node: SExpr) -> str:
    return (f'fill="{_fill(node)}" stroke="var(--symbol-line)" '
            f'stroke-width="{_stroke_width(node):.4f}" '
            'stroke-linecap="round" stroke-linejoin="round"')


def _rectangle(node: SExpr, canvas: Canvas) -> None:
    start, end = point(node.child("start")), point(node.child("end"))
    if not start or not end:
        return
    x, y = min(start[0], end[0]), min(start[1], end[1])
    w, h = abs(end[0] - start[0]), abs(end[1] - start[1])
    canvas.bounds.add(x, y)
    canvas.bounds.add(x + w, y + h)
    canvas.emit(f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" '
                f"{_attrs(node)}/>")


def _polyline(node: SExpr, canvas: Canvas) -> None:
    pts = node.child("pts")
    if pts is None:
        return
    points = [p for p in (point(c) for c in pts.children("xy")) if p]
    if len(points) < 2:
        return
    for p in points:
        canvas.bounds.add_point(p)
    path = " ".join(f"{x:.4f},{y:.4f}" for x, y in points)
    canvas.emit(f'<polyline points="{path}" {_attrs(node)}/>')


def _circle(node: SExpr, canvas: Canvas) -> None:
    center = point(node.child("center"))
    radius = floats(node.child("radius"))
    if not center or not radius:
        return
    cx, cy, r = center[0], center[1], radius[0]
    canvas.bounds.add(cx - r, cy - r)
    canvas.bounds.add(cx + r, cy + r)
    canvas.emit(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}" {_attrs(node)}/>')


def _arc(node: SExpr, canvas: Canvas) -> None:
    start, mid, end = (point(node.child(k)) for k in ("start", "mid", "end"))
    if not (start and mid and end):
        return
    for p in (start, mid, end):
        canvas.bounds.add_point(p)

    circle = circle_through(start, mid, end)
    if circle is None:  # collinear points describe a straight line
        canvas.emit(f'<polyline points="{start[0]:.4f},{start[1]:.4f} '
                    f'{end[0]:.4f},{end[1]:.4f}" {_attrs(node)}/>')
        return

    _, _, r = circle
    sweep = 1 - arc_sweep(start, mid, end)
    canvas.emit(f'<path d="M {start[0]:.4f},{start[1]:.4f} A {r:.4f},{r:.4f} 0 0 '
                f'{sweep} {end[0]:.4f},{end[1]:.4f}" {_attrs(node)}/>')


def _pin_number(node: SExpr) -> str:
    number = node.child("number")
    atoms = number.atoms() if number else []
    return atoms[0] if atoms else ""


def _pin(node: SExpr, canvas: Canvas) -> None:
    at = floats(node.child("at"))
    length = floats(node.child("length"))
    if len(at) < 3 or not length:
        return

    x, y, angle = at[0], at[1], at[2]
    rad = math.radians(angle)
    # (x, y) is the connection point; the pin runs from there toward the body.
    ex, ey = x + length[0] * math.cos(rad), y + length[0] * math.sin(rad)

    canvas.bounds.add(x, y)
    canvas.bounds.add(ex, ey)

    # Grouped and tagged so the same pin can be picked out in the footprint.
    number = _pin_number(node)
    tag = f' data-pin="{escape(number)}"' if number else ""
    canvas.emit(
        f'<g class="pin"{tag}>'
        f'<line x1="{x:.4f}" y1="{y:.4f}" x2="{ex:.4f}" y2="{ey:.4f}" '
        f'stroke="var(--symbol-line)" stroke-width="{DEFAULT_STROKE_MM:.4f}" '
        'stroke-linecap="round"/>'
        f'<circle cx="{x:.4f}" cy="{y:.4f}" r="{PIN_RADIUS_MM:.4f}" '
        'fill="none" stroke="var(--symbol-pin)" stroke-width="0.06"/>'
        "</g>"
    )


_DRAWERS = {
    "rectangle": _rectangle,
    "polyline": _polyline,
    "circle": _circle,
    "arc": _arc,
    "pin": _pin,
}


def _walk(node: SExpr, canvas: Canvas) -> None:
    for item in node.items:
        if not isinstance(item, SExpr):
            continue
        drawer = _DRAWERS.get(item.head or "")
        if drawer:
            drawer(item, canvas)
        elif item.head == "symbol":   # a unit
            _walk(item, canvas)


def render_symbol(symbol: SExpr, title: str = "") -> str:
    """Render one `(symbol ...)` node as a standalone SVG document."""
    canvas = Canvas()
    _walk(symbol, canvas)

    if canvas.bounds.empty:
        return placeholder(CSS_CLASS, title, STYLE, "no graphics")

    view = canvas.bounds.viewbox(MARGIN_MM)
    b = canvas.bounds
    # Flip once around the drawing's own centre rather than negating every point.
    flip = b.min_y + (b.max_y - b.min_y) / 2

    return document(
        css_class=CSS_CLASS,
        view=view,
        px_per_mm=PX_PER_MM,
        title=title,
        style=STYLE,
        body=f'<g transform="translate(0 {2 * flip:.4f}) scale(1 -1)">'
             f"{canvas.markup()}</g>",
    )

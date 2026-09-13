"""Draw a KiCad symbol as SVG.

Rendered from the parsed symbol rather than by shelling out to KiCad, which
would mean installing the whole application in the container to draw a
resistor.

Two things differ from SVG and catch people out: KiCad's Y axis points up, and
its units are millimetres. Both are handled once, in `_point`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.kicad.sexpr import SExpr

PX_PER_MM = 12.0
MARGIN_MM = 1.5
DEFAULT_STROKE_MM = 0.1524   # KiCad's default symbol line width
PIN_RADIUS_MM = 0.3


@dataclass
class _Bounds:
    min_x: float = math.inf
    min_y: float = math.inf
    max_x: float = -math.inf
    max_y: float = -math.inf

    def add(self, x: float, y: float) -> None:
        self.min_x = min(self.min_x, x)
        self.min_y = min(self.min_y, y)
        self.max_x = max(self.max_x, x)
        self.max_y = max(self.max_y, y)

    @property
    def empty(self) -> bool:
        return self.min_x is math.inf or self.min_x > self.max_x


@dataclass
class _Scene:
    parts: list[str] = field(default_factory=list)
    bounds: _Bounds = field(default_factory=_Bounds)


def _floats(node: SExpr | None) -> list[float]:
    if node is None:
        return []
    out = []
    for atom in node.atoms():
        try:
            out.append(float(atom))
        except ValueError:
            pass
    return out


def _xy(node: SExpr | None) -> tuple[float, float] | None:
    values = _floats(node)
    return (values[0], values[1]) if len(values) >= 2 else None


def _stroke_width(node: SExpr) -> float:
    stroke = node.child("stroke")
    width = _floats(stroke.child("width")) if stroke else []
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


def _shape_attrs(node: SExpr) -> str:
    return (f'fill="{_fill(node)}" stroke="var(--symbol-line)" '
            f'stroke-width="{_stroke_width(node):.4f}" '
            'stroke-linecap="round" stroke-linejoin="round"')


def _rectangle(node: SExpr, scene: _Scene) -> None:
    start, end = _xy(node.child("start")), _xy(node.child("end"))
    if not start or not end:
        return
    x, y = min(start[0], end[0]), min(start[1], end[1])
    w, h = abs(end[0] - start[0]), abs(end[1] - start[1])
    scene.bounds.add(x, y)
    scene.bounds.add(x + w, y + h)
    # Drawn in KiCad space; the whole picture is flipped once by the transform.
    scene.parts.append(
        f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" {_shape_attrs(node)}/>'
    )


def _polyline(node: SExpr, scene: _Scene) -> None:
    pts = node.child("pts")
    if pts is None:
        return
    points = [p for p in (_xy(c) for c in pts.children("xy")) if p]
    if len(points) < 2:
        return
    for x, y in points:
        scene.bounds.add(x, y)
    path = " ".join(f"{x:.4f},{y:.4f}" for x, y in points)
    scene.parts.append(f'<polyline points="{path}" {_shape_attrs(node)}/>')


def _circle(node: SExpr, scene: _Scene) -> None:
    center = _xy(node.child("center"))
    radius = _floats(node.child("radius"))
    if not center or not radius:
        return
    cx, cy, r = center[0], center[1], radius[0]
    scene.bounds.add(cx - r, cy - r)
    scene.bounds.add(cx + r, cy + r)
    scene.parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}" {_shape_attrs(node)}/>')


def _arc(node: SExpr, scene: _Scene) -> None:
    start, mid, end = (_xy(node.child(k)) for k in ("start", "mid", "end"))
    if not (start and mid and end):
        return
    for p in (start, mid, end):
        scene.bounds.add(*p)

    # KiCad stores three points on the arc; SVG wants a radius, so derive it
    # from the circle through them.
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:  # collinear: a straight line
        scene.parts.append(
            f'<polyline points="{x1:.4f},{y1:.4f} {x3:.4f},{y3:.4f}" {_shape_attrs(node)}/>'
        )
        return

    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1)
          + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3)
          + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)

    cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
    sweep = 0 if cross > 0 else 1
    large = 0

    scene.parts.append(
        f'<path d="M {x1:.4f},{y1:.4f} A {r:.4f},{r:.4f} 0 {large} {sweep} {x3:.4f},{y3:.4f}" '
        f"{_shape_attrs(node)}/>"
    )


def _pin(node: SExpr, scene: _Scene) -> None:
    at = _floats(node.child("at"))
    length = _floats(node.child("length"))
    if len(at) < 3 or not length:
        return

    x, y, angle = at[0], at[1], at[2]
    rad = math.radians(angle)
    # (x, y) is the connection point; the pin runs from there toward the body.
    ex, ey = x + length[0] * math.cos(rad), y + length[0] * math.sin(rad)

    scene.bounds.add(x, y)
    scene.bounds.add(ex, ey)
    scene.parts.append(
        f'<line x1="{x:.4f}" y1="{y:.4f}" x2="{ex:.4f}" y2="{ey:.4f}" '
        f'stroke="var(--symbol-line)" stroke-width="{DEFAULT_STROKE_MM:.4f}" '
        'stroke-linecap="round"/>'
    )
    scene.parts.append(
        f'<circle cx="{x:.4f}" cy="{y:.4f}" r="{PIN_RADIUS_MM:.4f}" '
        'fill="none" stroke="var(--symbol-pin)" stroke-width="0.06"/>'
    )


_DRAWERS = {
    "rectangle": _rectangle,
    "polyline": _polyline,
    "circle": _circle,
    "arc": _arc,
    "pin": _pin,
}


def _walk(node: SExpr, scene: _Scene) -> None:
    for item in node.items:
        if not isinstance(item, SExpr):
            continue
        drawer = _DRAWERS.get(item.head or "")
        if drawer:
            drawer(item, scene)
        elif item.head == "symbol":   # a unit
            _walk(item, scene)


def render_symbol(symbol: SExpr, title: str = "") -> str:
    """Render one `(symbol ...)` node as a standalone SVG document.

    Colours come from CSS variables so the drawing follows the page's theme
    instead of being baked light or dark.
    """
    scene = _Scene()
    _walk(symbol, scene)

    if scene.bounds.empty:
        return _empty_svg(title)

    b = scene.bounds
    min_x, min_y = b.min_x - MARGIN_MM, b.min_y - MARGIN_MM
    width = (b.max_x - b.min_x) + 2 * MARGIN_MM
    height = (b.max_y - b.min_y) + 2 * MARGIN_MM

    px_w, px_h = width * PX_PER_MM, height * PX_PER_MM

    # KiCad's Y axis points up; flip once around the drawing's own centre
    # rather than negating every coordinate.
    flip_axis = b.min_y + (b.max_y - b.min_y) / 2

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.4f} {min_y:.4f} '
        f'{width:.4f} {height:.4f}" width="{px_w:.0f}" height="{px_h:.0f}" '
        f'role="img" aria-label="{_escape(title or "symbol")}">'
        f"<title>{_escape(title)}</title>"
        f'<g transform="translate(0 {2 * flip_axis:.4f}) scale(1 -1)">'
        + "".join(scene.parts)
        + "</g></svg>"
    )


def _empty_svg(title: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20" width="120" height="60" '
        f'role="img" aria-label="{_escape(title)} has no graphics">'
        f"<title>{_escape(title)}</title>"
        '<text x="20" y="11" text-anchor="middle" font-size="4" '
        'fill="var(--symbol-pin)">no graphics</text></svg>'
    )


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

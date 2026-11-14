"""Draw a KiCad footprint as SVG.

Two things separate this from drawing a symbol. Board coordinates already run
with Y downwards, so nothing is flipped -- applying the symbol's transform
would mirror every part. And a footprint's meaning is in its layers, so they
are drawn in a fixed order and coloured apart rather than all in one ink.
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

CSS_CLASS = "kfp"
PX_PER_MM = 40.0
MARGIN_MM = 0.4
DEFAULT_STROKE_MM = 0.12

# Layer colours echo KiCad's own, so a footprint reads the way it does in the
# editor. Scoped to the drawing's class; the symbol renderer says why.
STYLE = (
    "<style>"
    ".kfp{--fp-copper:#c8842a;--fp-silk:#111418;--fp-courtyard:#b06cc0;"
    "--fp-fab:#9a8f7a;--fp-hole:#fdfdfd;--fp-hl:#2f6fd0}"
    "@media(prefers-color-scheme:dark){"
    ".kfp{--fp-copper:#d8973c;--fp-silk:#e8eaed;--fp-courtyard:#c58ad4;"
    "--fp-fab:#8a8170;--fp-hole:#14171c;--fp-hl:#6fa4f0}}"
    ".kfp .pad rect,.kfp .pad ellipse{transition:fill .1s}"
    ".kfp .pad.hl rect,.kfp .pad.hl ellipse{fill:var(--fp-hl)}"
    "</style>"
)

# Back to front. Copper is not listed because pads are always drawn last:
# they are what a footprint is for.
_LAYERS = {
    "F.CrtYd": ("var(--fp-courtyard)", 0.5),
    "B.CrtYd": ("var(--fp-courtyard)", 0.5),
    "F.Fab": ("var(--fp-fab)", 0.6),
    "B.Fab": ("var(--fp-fab)", 0.6),
    "F.SilkS": ("var(--fp-silk)", 1.0),
    "B.SilkS": ("var(--fp-silk)", 1.0),
}

# A layer with no entry above -- user drawings, an edge cut, adhesive -- is
# drawn faintly behind everything else instead of being dropped. It is part
# of the outline a reader is trying to recognise, and geometry that widens
# the view box without ever being painted leaves a margin nothing explains.
_OTHER_LAYER = ("var(--fp-fab)", 0.5)

# Mask and paste follow the copper and would only thicken the picture.
_SKIPPED_LAYERS = {"F.Mask", "B.Mask", "F.Paste", "B.Paste"}

_FILLED = {"yes", "solid", "true"}


class _Drawing:
    """Markup kept per layer, so it can be stacked in the right order."""

    def __init__(self) -> None:
        self.layers: dict[str, list[str]] = {}
        self.pads: list[str] = []
        self.bounds = Bounds()

    def on(self, layer: str, markup: str) -> None:
        self.layers.setdefault(layer, []).append(markup)

    def body(self) -> str:
        unranked = [layer for layer in self.layers if layer not in _LAYERS]
        ranked = [layer for layer in _LAYERS if layer in self.layers]
        return "".join(["".join(self.layers[layer]) for layer in (*unranked, *ranked)] + self.pads)


def _layer(node: SExpr) -> str:
    layer = node.child("layer")
    return (layer.arg(0, "") if layer else "") or ""


def _is_filled(node: SExpr) -> bool:
    """Whether a shape is filled, by its own `(fill ...)` setting.

    KiCad writes yes/no and, in older files, solid/none. Filling regardless
    turns the body outline on the fab layer into a slab that hides the pads.
    """
    fill = node.child("fill")
    if fill is None:
        return False
    if fill.args:
        return fill.args[0] in _FILLED
    kind = fill.child("type")
    return bool(kind and kind.arg(0) in ("solid", "background"))


def _paint(node: SExpr, layer: str) -> str:
    colour, opacity = _LAYERS.get(layer, _OTHER_LAYER)
    return (
        f'fill="{colour if _is_filled(node) else "none"}" stroke="{colour}" '
        f'stroke-width="{fmt(stroke_width(node, DEFAULT_STROKE_MM))}" opacity="{opacity}" '
        'stroke-linecap="round" stroke-linejoin="round"'
    )


def _segment(start: tuple[float, float], end: tuple[float, float], paint: str) -> str:
    return (
        f'<line x1="{fmt(start[0])}" y1="{fmt(start[1])}" '
        f'x2="{fmt(end[0])}" y2="{fmt(end[1])}" {paint}/>'
    )


def _line(node: SExpr, layer: str, drawing: _Drawing) -> None:
    start, end = point(node.child("start")), point(node.child("end"))
    if start is None or end is None:
        return
    drawing.bounds.include_all([start, end])
    drawing.on(layer, _segment(start, end, _paint(node, layer)))


def _rect(node: SExpr, layer: str, drawing: _Drawing) -> None:
    start, end = point(node.child("start")), point(node.child("end"))
    if start is None or end is None:
        return
    x, y = min(start[0], end[0]), min(start[1], end[1])
    width, height = abs(end[0] - start[0]), abs(end[1] - start[1])
    drawing.bounds.include_all([(x, y), (x + width, y + height)])
    drawing.on(
        layer,
        f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(width)}" height="{fmt(height)}" '
        f"{_paint(node, layer)}/>",
    )


def _circle(node: SExpr, layer: str, drawing: _Drawing) -> None:
    # A footprint circle is its centre and one point on the circumference.
    centre, edge = point(node.child("center")), point(node.child("end"))
    if centre is None or edge is None:
        return
    r = math.hypot(edge[0] - centre[0], edge[1] - centre[1])
    drawing.bounds.include_all([(centre[0] - r, centre[1] - r), (centre[0] + r, centre[1] + r)])
    drawing.on(
        layer,
        f'<circle cx="{fmt(centre[0])}" cy="{fmt(centre[1])}" r="{fmt(r)}" '
        f"{_paint(node, layer)}/>",
    )


def _arc(node: SExpr, layer: str, drawing: _Drawing) -> None:
    start, mid, end = (point(node.child(key)) for key in ("start", "mid", "end"))
    if start is None or mid is None or end is None:
        return
    drawing.bounds.include_all([start, mid, end])

    arc = arc_through(start, mid, end)
    if arc is None:
        drawing.on(layer, _segment(start, end, _paint(node, layer)))
        return

    drawing.on(
        layer,
        f'<path d="M {fmt(start[0])},{fmt(start[1])} '
        f"A {fmt(arc.radius)},{fmt(arc.radius)} 0 {arc.large} {arc.sweep} "
        f'{fmt(end[0])},{fmt(end[1])}" {_paint(node, layer)}/>',
    )


def _polygon(node: SExpr, layer: str, drawing: _Drawing) -> None:
    pts = points(node.child("pts"))
    if len(pts) < 3:
        return
    drawing.bounds.include_all(pts)
    path = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)
    drawing.on(layer, f'<polygon points="{path}" {_paint(node, layer)}/>')


_GRAPHICS: dict[str, Callable[[SExpr, str, _Drawing], None]] = {
    "fp_line": _line,
    "fp_rect": _rect,
    "fp_circle": _circle,
    "fp_arc": _arc,
    "fp_poly": _polygon,
}


def _pad(node: SExpr, drawing: _Drawing) -> None:
    # (pad "<number>" <type> <shape> (at x y [angle]) (size w h) ...)
    number, shape = node.arg(0, "") or "", node.arg(2, "rect")
    at, size = numbers(node.child("at")), numbers(node.child("size"))
    if len(at) < 2 or len(size) < 2:
        return

    x, y, angle = at[0], at[1], (at[2] if len(at) > 2 else 0.0)
    width, height = size[0], size[1]

    # Bounded by the pad's diagonal, which holds whatever angle it sits at.
    reach = math.hypot(width, height) / 2
    drawing.bounds.include_all([(x - reach, y - reach), (x + reach, y + reach)])

    rotate = f' transform="rotate({fmt(angle)} {fmt(x)} {fmt(y)})"' if angle else ""
    copper = 'fill="var(--fp-copper)" stroke="none"'

    # Grouped and tagged with its number so the matching symbol pin can find it.
    tag = f' data-pad="{escape(number)}"' if number else ""
    markup = [f'<g class="pad"{tag}>']

    if shape in ("circle", "oval"):
        markup.append(
            f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(width / 2)}" '
            f'ry="{fmt(height / 2)}" {copper}{rotate}/>'
        )
    else:
        ratio = numbers(node.child("roundrect_rratio"))
        corner = min(width, height) * (ratio[0] if ratio else 0.0)
        markup.append(
            f'<rect x="{fmt(x - width / 2)}" y="{fmt(y - height / 2)}" '
            f'width="{fmt(width)}" height="{fmt(height)}" rx="{fmt(corner)}" '
            f"{copper}{rotate}/>"
        )

    # A through-hole pad reads as a ring, so the barrel is punched out of it.
    drill = numbers(node.child("drill"))
    if drill:
        markup.append(
            f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(drill[0] / 2)}" '
            'fill="var(--fp-hole)" stroke="none"/>'
        )

    if number:
        markup.append(
            f'<text x="{fmt(x)}" y="{fmt(y)}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fmt(min(width, height) * 0.5)}" '
            'fill="var(--fp-hole)" font-family="system-ui, sans-serif">'
            f"{escape(number)}</text>"
        )

    markup.append("</g>")
    drawing.pads.append("".join(markup))


def render_footprint(footprint: SExpr, title: str = "") -> str:
    """Render a `(footprint ...)` node as a standalone SVG document."""
    drawing = _Drawing()

    for item in footprint.lists():
        if item.head == "pad":
            _pad(item, drawing)
            continue
        graphic = _GRAPHICS.get(item.head or "")
        if graphic is None:
            continue
        layer = _layer(item)
        if layer not in _SKIPPED_LAYERS:
            graphic(item, layer, drawing)

    if drawing.bounds.empty:
        return placeholder(css_class=CSS_CLASS, title=title, style=STYLE, message="no graphics")

    return document(
        css_class=CSS_CLASS,
        view_box=drawing.bounds.view_box(MARGIN_MM),
        px_per_mm=PX_PER_MM,
        title=title,
        style=STYLE,
        body=drawing.body(),
    )

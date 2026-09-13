"""Draw a KiCad footprint as SVG.

Two things separate this from symbol rendering. PCB coordinates already run
with Y downwards, so unlike a symbol nothing is flipped -- applying the symbol
transform would mirror every part. And a footprint's meaning is carried by its
layers, so they are drawn in a fixed order and coloured accordingly rather than
all in one ink.
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

PX_PER_MM = 40.0
MARGIN_MM = 0.4
DEFAULT_STROKE_MM = 0.12
CSS_CLASS = "kfp"

# Layer colours echo KiCad's own so a footprint reads the way it does in the
# editor. Scoped to the drawing's class; see the symbol renderer for why.
STYLE = (
    "<style>"
    ".kfp{--fp-copper:#c8842a;--fp-silk:#111418;--fp-courtyard:#b06cc0;"
    "--fp-fab:#9a8f7a;--fp-hole:#fdfdfd;--fp-text:#6b7480;--fp-hl:#2f6fd0}"
    "@media(prefers-color-scheme:dark){"
    ".kfp{--fp-copper:#d8973c;--fp-silk:#e8eaed;--fp-courtyard:#c58ad4;"
    "--fp-fab:#8a8170;--fp-hole:#14171c;--fp-text:#6b7480;--fp-hl:#6fa4f0}}"
    ".kfp .pad rect,.kfp .pad ellipse{transition:fill .1s}"
    ".kfp .pad.hl rect,.kfp .pad.hl ellipse{fill:var(--fp-hl)}"
    "</style>"
)

LAYER_STYLE = {
    "F.CrtYd": ("var(--fp-courtyard)", 0.5),
    "B.CrtYd": ("var(--fp-courtyard)", 0.5),
    "F.Fab": ("var(--fp-fab)", 0.6),
    "B.Fab": ("var(--fp-fab)", 0.6),
    "F.SilkS": ("var(--fp-silk)", 1.0),
    "B.SilkS": ("var(--fp-silk)", 1.0),
}

# Back to front, copper last: the pads are what a footprint is for.
LAYER_ORDER = ["F.CrtYd", "B.CrtYd", "F.Fab", "B.Fab", "F.SilkS", "B.SilkS"]

# Mask and paste track the copper and would only thicken the picture.
SKIP_LAYERS = {"F.Mask", "B.Mask", "F.Paste", "B.Paste"}

GRAPHIC_HEADS = ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly")


class _Scene(Canvas):
    """A canvas that keeps layers apart so they can be ordered on output."""

    def __init__(self) -> None:
        super().__init__()
        self.layers: dict[str, list[str]] = {}
        self.pads: list[str] = []

    def on_layer(self, layer: str, markup: str) -> None:
        self.layers.setdefault(layer, []).append(markup)


def _layer_of(node: SExpr) -> str:
    layer = node.child("layer")
    atoms = layer.atoms() if layer else []
    return atoms[0] if atoms else ""


def _stroke(node: SExpr) -> float:
    stroke = node.child("stroke")
    width = floats(stroke.child("width")) if stroke else []
    return width[0] if width and width[0] > 0 else DEFAULT_STROKE_MM


def _is_filled(node: SExpr) -> bool:
    """Whether the shape is filled, per its own (fill ...) setting.

    KiCad writes "yes"/"no" and, in older files, "solid"/"none". Filling
    everything turns a component body outline on the fab layer into a solid
    slab that hides the pads behind it.
    """
    fill = node.child("fill")
    if fill is None:
        return False
    atoms = fill.atoms()
    if atoms:
        return atoms[0] in ("yes", "solid", "true")
    kind = fill.child("type")
    return bool(kind and kind.atoms() and kind.atoms()[0] in ("solid", "background"))


def _attrs(layer: str, node: SExpr) -> str:
    colour, opacity = LAYER_STYLE.get(layer, ("var(--fp-fab)", 0.5))
    fill = colour if _is_filled(node) else "none"
    return (
        f'fill="{fill}" stroke="{colour}" stroke-width="{_stroke(node):.4f}" '
        f'opacity="{opacity}" stroke-linecap="round" stroke-linejoin="round"'
    )


def _fp_line(node: SExpr, scene: _Scene) -> None:
    start, end = point(node.child("start")), point(node.child("end"))
    if not start or not end:
        return
    scene.bounds.add_point(start)
    scene.bounds.add_point(end)
    layer = _layer_of(node)
    scene.on_layer(
        layer,
        f'<line x1="{start[0]:.4f}" y1="{start[1]:.4f}" '
        f'x2="{end[0]:.4f}" y2="{end[1]:.4f}" {_attrs(layer, node)}/>',
    )


def _fp_rect(node: SExpr, scene: _Scene) -> None:
    start, end = point(node.child("start")), point(node.child("end"))
    if not start or not end:
        return
    x, y = min(start[0], end[0]), min(start[1], end[1])
    w, h = abs(end[0] - start[0]), abs(end[1] - start[1])
    scene.bounds.add(x, y)
    scene.bounds.add(x + w, y + h)
    layer = _layer_of(node)
    scene.on_layer(
        layer,
        f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" '
        f'height="{h:.4f}" {_attrs(layer, node)}/>',
    )


def _fp_circle(node: SExpr, scene: _Scene) -> None:
    center, edge = point(node.child("center")), point(node.child("end"))
    if not center or not edge:
        return
    r = math.hypot(edge[0] - center[0], edge[1] - center[1])
    scene.bounds.add(center[0] - r, center[1] - r)
    scene.bounds.add(center[0] + r, center[1] + r)
    layer = _layer_of(node)
    scene.on_layer(
        layer,
        f'<circle cx="{center[0]:.4f}" cy="{center[1]:.4f}" '
        f'r="{r:.4f}" {_attrs(layer, node)}/>',
    )


def _fp_arc(node: SExpr, scene: _Scene) -> None:
    start, mid, end = (point(node.child(k)) for k in ("start", "mid", "end"))
    if not (start and mid and end):
        return
    for p in (start, mid, end):
        scene.bounds.add_point(p)

    layer = _layer_of(node)
    circle = circle_through(start, mid, end)
    if circle is None:
        scene.on_layer(
            layer,
            f'<line x1="{start[0]:.4f}" y1="{start[1]:.4f}" '
            f'x2="{end[0]:.4f}" y2="{end[1]:.4f}" {_attrs(layer, node)}/>',
        )
        return

    _, _, r = circle
    sweep = arc_sweep(start, mid, end)
    scene.on_layer(
        layer,
        f'<path d="M {start[0]:.4f},{start[1]:.4f} A {r:.4f},{r:.4f} '
        f'0 0 {sweep} {end[0]:.4f},{end[1]:.4f}" {_attrs(layer, node)}/>',
    )


def _fp_poly(node: SExpr, scene: _Scene) -> None:
    pts = node.child("pts")
    if pts is None:
        return
    points = [p for p in (point(c) for c in pts.children("xy")) if p]
    if len(points) < 3:
        return
    for p in points:
        scene.bounds.add_point(p)
    layer = _layer_of(node)
    path = " ".join(f"{x:.4f},{y:.4f}" for x, y in points)
    scene.on_layer(layer, f'<polygon points="{path}" {_attrs(layer, node)}/>')


def _pad(node: SExpr, scene: _Scene) -> None:
    atoms = node.atoms()
    shape = atoms[2] if len(atoms) >= 3 else "rect"
    number = atoms[0] if atoms else ""

    at = floats(node.child("at"))
    size = floats(node.child("size"))
    if len(at) < 2 or len(size) < 2:
        return

    x, y = at[0], at[1]
    angle = at[2] if len(at) > 2 else 0.0
    w, h = size[0], size[1]

    half = math.hypot(w, h) / 2
    scene.bounds.add(x - half, y - half)
    scene.bounds.add(x + half, y + half)

    rotate = f' transform="rotate({angle:.4f} {x:.4f} {y:.4f})"' if angle else ""
    copper = 'fill="var(--fp-copper)" stroke="none"'

    # Grouped and tagged so the matching symbol pin can highlight it.
    tag = f' data-pad="{escape(number)}"' if number else ""
    scene.pads.append(f'<g class="pad"{tag}>')

    if shape in ("circle", "oval"):
        scene.pads.append(
            f'<ellipse cx="{x:.4f}" cy="{y:.4f}" rx="{w / 2:.4f}" '
            f'ry="{h / 2:.4f}" {copper}{rotate}/>'
        )
    else:
        ratio = floats(node.child("roundrect_rratio"))
        radius = min(w, h) * (ratio[0] if ratio else 0.0)
        scene.pads.append(
            f'<rect x="{x - w / 2:.4f}" y="{y - h / 2:.4f}" '
            f'width="{w:.4f}" height="{h:.4f}" rx="{radius:.4f}" '
            f"{copper}{rotate}/>"
        )

    # A through-hole pad reads as a ring, so punch the barrel out of it.
    drill = node.child("drill")
    if drill is not None:
        sizes = floats(drill)
        if sizes:
            scene.pads.append(
                f'<circle cx="{x:.4f}" cy="{y:.4f}" r="{sizes[0] / 2:.4f}" '
                'fill="var(--fp-hole)" stroke="none"/>'
            )

    if number:
        scene.pads.append(
            f'<text x="{x:.4f}" y="{y:.4f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{min(w, h) * 0.5:.4f}" '
            f'fill="var(--fp-hole)" font-family="system-ui, sans-serif">'
            f"{escape(number)}</text>"
        )

    scene.pads.append("</g>")


_DRAWERS = {
    "fp_line": _fp_line,
    "fp_rect": _fp_rect,
    "fp_circle": _fp_circle,
    "fp_arc": _fp_arc,
    "fp_poly": _fp_poly,
    "pad": _pad,
}


def render_footprint(footprint: SExpr, title: str = "") -> str:
    """Render a `(footprint ...)` node as a standalone SVG document."""
    scene = _Scene()
    for item in footprint.items:
        if not isinstance(item, SExpr):
            continue
        if item.head in GRAPHIC_HEADS and _layer_of(item) in SKIP_LAYERS:
            continue
        drawer = _DRAWERS.get(item.head or "")
        if drawer:
            drawer(item, scene)

    if scene.bounds.empty:
        return placeholder(CSS_CLASS, title, STYLE, "no graphics")

    body = "".join("".join(scene.layers.get(layer, [])) for layer in LAYER_ORDER)
    body += "".join(scene.pads)

    return document(
        css_class=CSS_CLASS,
        view=scene.bounds.viewbox(MARGIN_MM),
        px_per_mm=PX_PER_MM,
        title=title,
        style=STYLE,
        body=body,
    )

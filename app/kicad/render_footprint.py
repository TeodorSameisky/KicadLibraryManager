"""Draw a KiCad footprint as SVG.

Two things separate this from symbol rendering. PCB coordinates already run
with Y downwards, so unlike a symbol nothing is flipped. And what a footprint
means is carried by its layers -- copper, silkscreen, courtyard -- so they are
drawn in that order and coloured accordingly rather than all in one ink.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.kicad.render import _escape, _floats, _xy
from app.kicad.sexpr import SExpr

PX_PER_MM = 40.0
MARGIN_MM = 0.4
DEFAULT_STROKE_MM = 0.12

# An <img>-embedded SVG inherits nothing from the page, so the palette travels
# with the file. Layer colours echo KiCad's own so a footprint reads the way it
# does in the editor.
# Scoped to the root element's own class rather than :root, so that inlining
# the drawing into a page does not redefine the page's variables.
STYLE = (
    "<style>"
    ".kfp{"
    "--fp-copper:#c8842a;--fp-silk:#111418;--fp-courtyard:#b06cc0;"
    "--fp-fab:#9a8f7a;--fp-hole:#fdfdfd;--fp-text:#6b7480;--fp-hl:#2f6fd0"
    "}"
    "@media(prefers-color-scheme:dark){"
    ".kfp{"
    "--fp-copper:#d8973c;--fp-silk:#e8eaed;--fp-courtyard:#c58ad4;"
    "--fp-fab:#8a8170;--fp-hole:#14171c;--fp-text:#6b7480;--fp-hl:#6fa4f0"
    "}}"
    ".pad rect,.pad ellipse{transition:fill .1s}"
    ".pad.hl rect,.pad.hl ellipse{fill:var(--fp-hl)}"
    "</style>"
)

# Drawn back to front. Mask and paste are omitted: they track the copper and
# would only thicken the picture.
LAYER_STYLE = {
    "F.CrtYd": ("var(--fp-courtyard)", 0.5),
    "B.CrtYd": ("var(--fp-courtyard)", 0.5),
    "F.Fab": ("var(--fp-fab)", 0.6),
    "B.Fab": ("var(--fp-fab)", 0.6),
    "F.SilkS": ("var(--fp-silk)", 1.0),
    "B.SilkS": ("var(--fp-silk)", 1.0),
}
LAYER_ORDER = ["F.CrtYd", "B.CrtYd", "F.Fab", "B.Fab", "F.SilkS", "B.SilkS"]
SKIP_LAYERS = {"F.Mask", "B.Mask", "F.Paste", "B.Paste"}


@dataclass
class _Bounds:
    min_x: float = math.inf
    min_y: float = math.inf
    max_x: float = -math.inf
    max_y: float = -math.inf

    def add(self, x: float, y: float) -> None:
        self.min_x, self.min_y = min(self.min_x, x), min(self.min_y, y)
        self.max_x, self.max_y = max(self.max_x, x), max(self.max_y, y)

    @property
    def empty(self) -> bool:
        return self.min_x > self.max_x


@dataclass
class _Scene:
    layers: dict[str, list[str]] = field(default_factory=dict)
    pads: list[str] = field(default_factory=list)
    bounds: _Bounds = field(default_factory=_Bounds)

    def draw(self, layer: str, markup: str) -> None:
        self.layers.setdefault(layer, []).append(markup)


def _layer_of(node: SExpr) -> str:
    layer = node.child("layer")
    atoms = layer.atoms() if layer else []
    return atoms[0] if atoms else ""


def _stroke(node: SExpr) -> float:
    stroke = node.child("stroke")
    width = _floats(stroke.child("width")) if stroke else []
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


def _graphic_attrs(layer: str, node: SExpr, filled: bool | None = None) -> str:
    colour, opacity = LAYER_STYLE.get(layer, ("var(--fp-fab)", 0.5))
    if filled is None:
        filled = _is_filled(node)
    fill = colour if filled else "none"
    return (f'fill="{fill}" stroke="{colour}" stroke-width="{_stroke(node):.4f}" '
            f'opacity="{opacity}" stroke-linecap="round" stroke-linejoin="round"')


def _fp_line(node: SExpr, scene: _Scene) -> None:
    start, end = _xy(node.child("start")), _xy(node.child("end"))
    if not start or not end:
        return
    scene.bounds.add(*start)
    scene.bounds.add(*end)
    layer = _layer_of(node)
    scene.draw(layer, f'<line x1="{start[0]:.4f}" y1="{start[1]:.4f}" '
                      f'x2="{end[0]:.4f}" y2="{end[1]:.4f}" {_graphic_attrs(layer, node)}/>')


def _fp_rect(node: SExpr, scene: _Scene) -> None:
    start, end = _xy(node.child("start")), _xy(node.child("end"))
    if not start or not end:
        return
    x, y = min(start[0], end[0]), min(start[1], end[1])
    w, h = abs(end[0] - start[0]), abs(end[1] - start[1])
    scene.bounds.add(x, y)
    scene.bounds.add(x + w, y + h)
    layer = _layer_of(node)
    scene.draw(layer, f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" '
                      f"{_graphic_attrs(layer, node)}/>")


def _fp_circle(node: SExpr, scene: _Scene) -> None:
    center, edge = _xy(node.child("center")), _xy(node.child("end"))
    if not center or not edge:
        return
    r = math.hypot(edge[0] - center[0], edge[1] - center[1])
    scene.bounds.add(center[0] - r, center[1] - r)
    scene.bounds.add(center[0] + r, center[1] + r)
    layer = _layer_of(node)
    scene.draw(layer, f'<circle cx="{center[0]:.4f}" cy="{center[1]:.4f}" r="{r:.4f}" '
                      f"{_graphic_attrs(layer, node)}/>")


def _fp_arc(node: SExpr, scene: _Scene) -> None:
    start, mid, end = (_xy(node.child(k)) for k in ("start", "mid", "end"))
    if not (start and mid and end):
        return
    for p in (start, mid, end):
        scene.bounds.add(*p)

    layer = _layer_of(node)
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        scene.draw(layer, f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x3:.4f}" y2="{y3:.4f}" '
                          f"{_graphic_attrs(layer, node)}/>")
        return

    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1)
          + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3)
          + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
    sweep = 1 if cross > 0 else 0

    scene.draw(layer, f'<path d="M {x1:.4f},{y1:.4f} A {r:.4f},{r:.4f} 0 0 {sweep} '
                      f'{x3:.4f},{y3:.4f}" {_graphic_attrs(layer, node)}/>')


def _fp_poly(node: SExpr, scene: _Scene) -> None:
    pts = node.child("pts")
    if pts is None:
        return
    points = [p for p in (_xy(c) for c in pts.children("xy")) if p]
    if len(points) < 3:
        return
    for p in points:
        scene.bounds.add(*p)
    layer = _layer_of(node)
    path = " ".join(f"{x:.4f},{y:.4f}" for x, y in points)
    scene.draw(layer, f'<polygon points="{path}" {_graphic_attrs(layer, node)}/>')


def _pad(node: SExpr, scene: _Scene) -> None:
    atoms = node.atoms()
    shape = atoms[2] if len(atoms) >= 3 else "rect"
    number = atoms[0] if atoms else ""

    at = _floats(node.child("at"))
    size = _floats(node.child("size"))
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
    tag = f' data-pad="{_escape(number)}"' if number else ""
    scene.pads.append(f'<g class="pad"{tag}>')

    if shape in ("circle", "oval"):
        scene.pads.append(
            f'<ellipse cx="{x:.4f}" cy="{y:.4f}" rx="{w / 2:.4f}" ry="{h / 2:.4f}" '
            f"{copper}{rotate}/>"
        )
    else:
        ratio = _floats(node.child("roundrect_rratio"))
        radius = min(w, h) * (ratio[0] if ratio else 0.0)
        scene.pads.append(
            f'<rect x="{x - w / 2:.4f}" y="{y - h / 2:.4f}" width="{w:.4f}" height="{h:.4f}" '
            f'rx="{radius:.4f}" {copper}{rotate}/>'
        )

    # A through-hole pad reads as a ring, so punch the barrel out of it.
    drill = node.child("drill")
    if drill is not None:
        sizes = _floats(drill)
        if sizes:
            scene.pads.append(
                f'<circle cx="{x:.4f}" cy="{y:.4f}" r="{sizes[0] / 2:.4f}" '
                'fill="var(--fp-hole)" stroke="none"/>'
            )

    if number:
        scene.pads.append(
            f'<text x="{x:.4f}" y="{y:.4f}" text-anchor="middle" dominant-baseline="central" '
            f'font-size="{min(w, h) * 0.5:.4f}" fill="var(--fp-hole)" '
            f'font-family="system-ui, sans-serif">{_escape(number)}</text>'
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
        if item.head in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
            if _layer_of(item) in SKIP_LAYERS:
                continue
        drawer = _DRAWERS.get(item.head or "")
        if drawer:
            drawer(item, scene)

    if scene.bounds.empty:
        return _empty_svg(title)

    b = scene.bounds
    min_x, min_y = b.min_x - MARGIN_MM, b.min_y - MARGIN_MM
    width = (b.max_x - b.min_x) + 2 * MARGIN_MM
    height = (b.max_y - b.min_y) + 2 * MARGIN_MM

    body = "".join(
        "".join(scene.layers.get(layer, [])) for layer in LAYER_ORDER
    )
    # Copper last: the pads are what a footprint is for.
    body += "".join(scene.pads)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.4f} {min_y:.4f} '
        f'{width:.4f} {height:.4f}" width="{width * PX_PER_MM:.0f}" '
        f'height="{height * PX_PER_MM:.0f}" class="kfp" role="img" '
        f'aria-label="{_escape(title or "footprint")}">'
        f"<title>{_escape(title)}</title>{STYLE}{body}</svg>"
    )


def _empty_svg(title: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20" width="160" height="80" '
        f'class="kfp" role="img" aria-label="{_escape(title)} has no drawable items">'
        f"<title>{_escape(title)}</title>{STYLE}"
        '<text x="20" y="11" text-anchor="middle" font-size="4" '
        'fill="var(--fp-text)">no graphics</text></svg>'
    )

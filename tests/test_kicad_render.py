"""Drawing symbols and footprints as SVG."""

import math
import re

import pytest

from app.kicad.render import render_footprint, render_symbol
from app.kicad.render.canvas import arc_through
from app.kicad.sexpr import loads


def symbol(body: str, title: str = "X") -> str:
    return render_symbol(loads(f'(symbol "X" (symbol "X_1_1" {body}))'), title)


def footprint(body: str) -> str:
    return render_footprint(loads(f'(footprint "FP" {body})'), "FP")


def drawn(svg: str) -> str:
    """The markup after the style block: what was actually drawn."""
    return svg.split("</style>", 1)[1]


# -- arcs -------------------------------------------------------------------


def passes_through(start, end, arc, point) -> bool:
    """Whether the SVG arc these flags describe goes through `point`.

    Worked out from the SVG specification's own construction of an arc's
    centre, independently of the code under test.
    """
    (x1, y1), (x2, y2), r = start, end, arc.radius
    half = math.dist(start, end) / 2
    height = math.sqrt(max(r * r - half * half, 0))
    sign = 1 if arc.large != arc.sweep else -1
    cx = (x1 + x2) / 2 + sign * height * (y1 - y2) / (2 * half)
    cy = (y1 + y2) / 2 + sign * height * (x2 - x1) / (2 * half)

    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)

    turn = 2 * math.pi
    # How far round from the start, in the direction the arc is drawn.
    travelled = (angle(point) - angle(start)) % turn
    whole = (angle(end) - angle(start)) % turn
    if not arc.sweep:
        travelled, whole = (turn - travelled) % turn, (turn - whole) % turn

    on_circle = math.isclose(math.dist(point, (cx, cy)), r, abs_tol=1e-3)
    return on_circle and travelled <= whole + 1e-6


@pytest.mark.parametrize(
    "start,mid,end",
    [
        ((-1, 0), (0, 1), (1, 0)),  # a half turn, over the top
        ((-1, 0), (0, -1), (1, 0)),  # the same chord, underneath
        ((1, 0), (0, 1), (-1, 0)),  # the first one, drawn backwards
        ((-2.032, -1.27), (0, -0.5572), (2.032, -1.27)),  # the plate of a polarised capacitor
        ((0, 1), (-1, 0), (0.7071, -0.7071)),  # most of a circle
        ((0.7071, -0.7071), (-1, 0), (0, 1)),  # and the same, the other way round
    ],
)
def test_an_arc_passes_through_its_middle_point(start, mid, end):
    """KiCad gives three points on the curve. Both flags have to be right, or
    the arc bulges to the wrong side of its chord or takes the short way."""
    arc = arc_through(start, mid, end)

    assert passes_through(start, end, arc, mid)
    # And not through the point opposite, which the other three combinations
    # of the two flags would each give for some of these.
    opposite = (start[0] + end[0] - mid[0], start[1] + end[1] - mid[1])
    assert not passes_through(start, end, arc, opposite)


def test_points_in_a_line_have_no_arc():
    assert arc_through((-1, 0), (0, 0), (1, 0)) is None


# -- symbols ----------------------------------------------------------------


def test_a_rectangle_is_drawn_and_frames_the_view():
    svg = symbol("(rectangle (start -1 -2) (end 1 2))")

    assert "<rect" in svg
    assert 'viewBox="-2.5000 -3.5000 5.0000 7.0000"' in svg, "with a margin around it"


def test_polylines_circles_and_arcs_are_drawn():
    svg = symbol(
        "(polyline (pts (xy -2 0) (xy 2 0)))"
        "(circle (center 0 0) (radius 1))"
        "(arc (start -1 0) (mid 0 1) (end 1 0))"
    )

    assert "<polyline" in svg and "<circle" in svg
    assert re.search(r'<path d="M -1.0000,0.0000 A 1.0000,1.0000 0 [01] [01] 1.0000,0.0000"', svg)


def test_an_arc_through_points_in_a_line_becomes_the_line():
    """There is no circle through them, and SVG would draw nothing at all."""
    svg = symbol("(arc (start -1 0) (mid 0 0) (end 1 0))")

    assert "<polyline" in svg and " A " not in svg


def test_a_pin_runs_from_where_the_wire_attaches_toward_the_body():
    svg = symbol('(pin passive line (at 0 3.81 270) (length 1.27) (number "1"))')

    assert 'y1="3.8100"' in svg
    assert 'y2="2.5400"' in svg, "270 degrees points down, at the body"


def test_pins_are_tagged_with_their_number():
    """So the page can find the pad each one maps to."""
    svg = symbol('(pin passive line (at 0 3.81 270) (length 1.27) (number "2"))')
    assert '<g class="pin" data-pin="2">' in svg


def test_the_drawing_is_flipped_once():
    """A symbol's Y axis points up and SVG's points down."""
    assert "scale(1 -1)" in symbol("(rectangle (start -1 -2) (end 1 2))")


def test_a_zero_line_width_means_the_default():
    """Not invisible, which is what zero would be if taken literally."""
    svg = symbol("(rectangle (start 0 0) (end 1 1) (stroke (width 0)))")
    assert 'stroke-width="0.1524"' in svg


@pytest.mark.parametrize(
    "fill,expected",
    [("none", "none"), ("background", "var(--symbol-fill)"), ("outline", "var(--symbol-line)")],
)
def test_fills(fill, expected):
    svg = symbol(f"(rectangle (start 0 0) (end 1 1) (fill (type {fill})))")
    assert f'fill="{expected}"' in svg


def test_every_unit_is_drawn():
    svg = render_symbol(
        loads(
            '(symbol "X"'
            ' (symbol "X_1_1" (rectangle (start 0 0) (end 1 1)))'
            ' (symbol "X_2_1" (circle (center 5 5) (radius 1))))'
        )
    )
    assert "<rect" in svg and "<circle" in svg


def test_a_symbol_with_nothing_to_draw_says_so():
    svg = render_symbol(loads('(symbol "X" (property "Value" "X"))'), "X")

    assert svg.startswith("<svg")
    assert "no graphics" in svg


def test_text_from_the_library_is_escaped():
    svg = symbol("(circle (center 0 0) (radius 1))", title="R & <script>")

    assert "&amp;" in svg and "&lt;script&gt;" in svg
    assert "<script>" not in svg


@pytest.mark.parametrize(
    "svg,scope",
    [
        (symbol("(rectangle (start 0 0) (end 1 1))"), "ksym"),
        (render_symbol(loads('(symbol "X")')), "ksym"),
        (footprint('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'), "kfp"),
    ],
    ids=["symbol", "empty-symbol", "footprint"],
)
def test_a_drawing_carries_its_own_palette(svg, scope):
    """An SVG inside an <img> inherits nothing from the page. Without a style
    block of its own every stroke is an undefined variable, and the drawing is
    an empty box -- which is exactly what happened.

    It is scoped to the drawing's class, so that inlining the same markup into
    a page does not redefine that page's own variables."""
    assert f'class="{scope}"' in svg
    assert f"<style>.{scope}{{--" in svg
    assert "prefers-color-scheme:dark" in svg
    assert ":root" not in svg


def test_shapes_name_colours_and_do_not_contain_them():
    body = drawn(symbol("(rectangle (start 0 0) (end 1 1))"))

    assert "var(--symbol-line)" in body
    assert "#" not in body


# -- footprints -------------------------------------------------------------


def test_pads_are_copper_and_numbered():
    svg = footprint('(pad "1" smd rect (at 0 0) (size 1 2) (layers "F.Cu"))')

    assert '<g class="pad" data-pad="1">' in svg
    assert 'fill="var(--fp-copper)"' in svg
    assert ">1</text>" in svg


def test_a_footprint_is_not_flipped():
    """Board coordinates already run with Y down. The symbol's transform
    would mirror every part."""
    svg = footprint('(fp_line (start 0 -1) (end 0 1) (layer "F.SilkS"))')

    assert "scale(1 -1)" not in svg
    assert 'y1="-1.0000"' in svg


def test_a_through_hole_pad_has_its_barrel_punched_out():
    svg = footprint('(pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1) (layers "*.Cu"))')

    assert "<ellipse" in svg
    assert 'r="0.5000" fill="var(--fp-hole)"' in svg, "or it reads as a solid disc"


def test_rounded_and_rotated_pads():
    rounded = footprint(
        '(pad "1" smd roundrect (at 0 0) (size 1 2) (roundrect_rratio 0.25) (layers "F.Cu"))'
    )
    rotated = footprint('(pad "1" smd rect (at 0 0 90) (size 1 2) (layers "F.Cu"))')

    assert 'rx="0.2500"' in rounded
    assert "rotate(90.0000 0.0000 0.0000)" in rotated


@pytest.mark.parametrize("fill,filled", [("yes", True), ("no", False), ("solid", True)])
def test_a_shape_is_filled_only_if_it_says_so(fill, filled):
    """Filling a body outline on the fab layer hides the pads behind a slab."""
    svg = footprint(f'(fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (fill {fill}) (layer "F.Fab"))')
    assert ('fill="var(--fp-fab)"' in svg) is filled


def test_layers_are_stacked_courtyard_then_silk_then_copper():
    """Whatever order the file lists them in. The pads are what a footprint
    is for, so they go on top."""
    body = drawn(
        footprint(
            '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
            '(fp_line (start 0 0) (end 1 0) (layer "F.SilkS"))'
            '(fp_rect (start -1 -1) (end 2 1) (layer "F.CrtYd"))'
        )
    )

    order = [body.index(f"var(--fp-{name})") for name in ("courtyard", "silk", "copper")]
    assert order == sorted(order)


def test_mask_and_paste_are_left_out():
    """They follow the copper and would only thicken the picture -- and must
    not stretch the view to fit something that is not drawn."""
    svg = footprint(
        '(fp_line (start -9 -9) (end 9 -9) (layer "F.Paste"))'
        '(fp_line (start -9 9) (end 9 9) (layer "B.Mask"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
    )

    assert "<line" not in svg
    assert 'viewBox="-9' not in svg


def test_a_layer_with_no_place_in_the_order_is_still_drawn():
    """It was measured into the view and then dropped, which left the drawing
    with a margin that nothing in it accounted for."""
    svg = footprint(
        '(fp_line (start -20 -20) (end 20 -20) (layer "Dwgs.User"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
    )
    body = drawn(svg)

    assert 'viewBox="-20' in svg
    assert body.index("<line") < body.index('class="pad"'), "behind everything else"


def test_footprint_arcs_and_circles():
    svg = footprint(
        '(fp_arc (start -1 0) (mid 0 1) (end 1 0) (layer "F.SilkS"))'
        '(fp_circle (center 0 0) (end 1 0) (layer "F.SilkS"))'
    )

    assert "<path" in svg and " A 1.0000,1.0000 " in svg
    assert '<circle cx="0.0000" cy="0.0000" r="1.0000"' in svg


def test_a_footprint_with_nothing_to_draw_says_so():
    assert "no graphics" in render_footprint(loads('(footprint "FP" (descr "empty"))'), "FP")

"""Drawing symbols as SVG."""

import pytest

from app.kicad.render import render_symbol
from app.kicad.sexpr import loads


def draw(body: str) -> str:
    return render_symbol(loads(f'(symbol "X" {body})'), "X")


def test_rectangle_is_drawn_and_bounds_the_view():
    svg = draw("(symbol \"X_1_1\" (rectangle (start -1 -2) (end 1 2)))")

    assert "<rect" in svg
    assert 'viewBox="-2.5000 -3.5000 5.0000 7.0000"' in svg, "includes a margin"


def test_polyline_and_circle_and_arc():
    svg = draw(
        '(symbol "X_1_1"'
        " (polyline (pts (xy -2 0) (xy 2 0)))"
        " (circle (center 0 0) (radius 1))"
        " (arc (start -1 0) (mid 0 1) (end 1 0)))"
    )

    assert "<polyline" in svg
    assert "<circle" in svg
    assert "<path" in svg and " A " in svg, "an arc becomes an SVG arc segment"


def test_a_collinear_arc_degrades_to_a_line():
    """Three points on a line have no circumcircle; SVG would render nothing."""
    svg = draw('(symbol "X_1_1" (arc (start -1 0) (mid 0 0) (end 1 0)))')

    assert "<polyline" in svg
    assert " A " not in svg


def test_pin_runs_from_its_connection_point_toward_the_body():
    """KiCad's angle is the direction the pin extends from the connection."""
    svg = draw('(symbol "X_1_1" (pin passive line (at 0 3.81 270) (length 1.27)))')

    assert 'y1="3.8100"' in svg
    assert 'y2="2.5400"' in svg, "270 degrees points down, toward the body"


def test_the_y_axis_is_flipped_for_svg():
    """KiCad's Y points up; without this every symbol renders upside down."""
    svg = draw('(symbol "X_1_1" (rectangle (start -1 -2) (end 1 2)))')

    assert "scale(1 -1)" in svg


def test_zero_stroke_width_uses_the_default():
    """Zero means 'default' in KiCad, not 'invisible'."""
    svg = draw('(symbol "X_1_1" (rectangle (start 0 0) (end 1 1) (stroke (width 0))))')

    assert 'stroke-width="0.1524"' in svg


@pytest.mark.parametrize(
    "fill,expected",
    [("none", "none"), ("background", "var(--symbol-fill)"), ("outline", "var(--symbol-line)")],
)
def test_fill_types(fill, expected):
    svg = draw(f'(symbol "X_1_1" (rectangle (start 0 0) (end 1 1) (fill (type {fill}))))')
    assert f'fill="{expected}"' in svg


def test_colours_come_from_css_variables():
    """So the drawing follows the page theme instead of being baked light."""
    svg = draw('(symbol "X_1_1" (rectangle (start 0 0) (end 1 1)))')

    assert "var(--symbol-line)" in svg
    assert "#" not in svg, "no hardcoded colours"


def test_units_are_walked():
    svg = draw('(symbol "X_1_1" (rectangle (start 0 0) (end 1 1)))')
    assert "<rect" in svg


def test_a_symbol_with_no_graphics_says_so():
    svg = render_symbol(loads('(symbol "X" (property "Value" "X"))'), "X")

    assert "no graphics" in svg
    assert svg.startswith("<svg")


def test_the_title_is_escaped():
    svg = render_symbol(loads('(symbol "X" (symbol "X_1_1" (circle (center 0 0) (radius 1))))'),
                        'R & <script>')

    assert "&amp;" in svg and "&lt;script&gt;" in svg
    assert "<script>" not in svg

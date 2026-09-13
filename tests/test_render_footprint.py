"""Drawing footprints as SVG."""

import pytest

from app.kicad.render import render_footprint
from app.kicad.sexpr import loads


def draw(body: str) -> str:
    return render_footprint(loads(f'(footprint "FP" {body})'), "FP")


def test_pads_are_drawn_in_copper():
    svg = draw('(pad "1" smd rect (at 0 0) (size 1 2) (layers "F.Cu"))')

    assert "<rect" in svg
    assert "var(--fp-copper)" in svg
    assert ">1</text>" in svg, "the pad number is labelled"


def test_the_y_axis_is_not_flipped():
    """PCB coordinates already run with Y downwards, unlike symbols."""
    svg = draw('(fp_line (start 0 -1) (end 0 1) (layer "F.SilkS"))')

    assert "scale(1 -1)" not in svg
    assert 'y1="-1.0000"' in svg


def test_a_through_hole_pad_gets_its_barrel_punched_out():
    svg = draw('(pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1) (layers "*.Cu"))')

    assert "var(--fp-hole)" in svg, "otherwise it reads as a solid disc"
    assert 'r="0.5000"' in svg


def test_roundrect_pads_are_rounded():
    svg = draw(
        '(pad "1" smd roundrect (at 0 0) (size 1 2) (roundrect_rratio 0.25) ' '(layers "F.Cu"))'
    )

    assert 'rx="0.2500"' in svg


def test_rotated_pads_are_rotated():
    svg = draw('(pad "1" smd rect (at 0 0 90) (size 1 2) (layers "F.Cu"))')

    assert "rotate(90" in svg


@pytest.mark.parametrize("fill,filled", [("yes", True), ("no", False)])
def test_polygons_respect_their_fill_setting(fill, filled):
    """Filling a fab-layer body outline hides the pads behind a solid slab."""
    svg = draw(f'(fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (fill {fill}) (layer "F.Fab"))')

    assert ('fill="var(--fp-fab)"' in svg) is filled


def test_mask_and_paste_layers_are_omitted():
    """They track the copper and only thicken the picture."""
    svg = draw(
        '(fp_line (start 0 0) (end 1 1) (layer "F.Paste"))'
        '(fp_line (start 0 0) (end 2 2) (layer "F.SilkS"))'
    )

    assert svg.count("<line") == 1


def test_silkscreen_is_drawn_over_the_courtyard():
    svg = draw(
        '(fp_line (start 0 0) (end 1 0) (layer "F.SilkS"))'
        '(fp_rect (start -1 -1) (end 2 1) (layer "F.CrtYd"))'
    )

    assert svg.index("var(--fp-courtyard)") < svg.index("var(--fp-silk)")


def test_copper_is_drawn_last():
    """The pads are what a footprint is for."""
    svg = draw(
        '(fp_line (start 0 0) (end 1 0) (layer "F.SilkS"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
    )

    assert svg.index("var(--fp-silk)") < svg.index("var(--fp-copper)")


def test_the_palette_travels_inside_the_svg():
    svg = draw('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))')

    assert "<style>" in svg
    assert "prefers-color-scheme:dark" in svg


def test_a_footprint_with_nothing_drawable():
    svg = render_footprint(loads('(footprint "FP" (descr "empty"))'), "FP")

    assert "no graphics" in svg


def test_arc_and_circle_are_drawn():
    svg = draw(
        '(fp_arc (start -1 0) (mid 0 1) (end 1 0) (layer "F.SilkS"))'
        '(fp_circle (center 0 0) (end 1 0) (layer "F.SilkS"))'
    )

    assert "<path" in svg and " A " in svg
    assert "<circle" in svg


def test_pads_are_grouped_and_numbered():
    svg = draw('(pad "3" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))')

    assert '<g class="pad" data-pad="3">' in svg
    assert svg.count("</g>") >= 1


def test_the_palette_is_scoped_to_the_drawing():
    svg = draw('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))')

    assert ".kfp{" in svg
    assert ":root{" not in svg
    assert 'class="kfp"' in svg


def test_a_layer_with_no_declared_order_is_still_drawn():
    """It was measured into the bounding box and then dropped, which left the
    drawing with a margin nothing in it accounted for."""
    svg = draw(
        '(fp_line (start -5 -5) (end 5 -5) (layer "User.Drawings"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
    )

    assert 'y1="-5.0000"' in svg, "the User.Drawings line is drawn"


def test_a_dropped_layer_would_have_stretched_the_viewbox():
    """Guards the pairing: whatever sets the extent has to appear in it."""
    with_line = draw(
        '(fp_line (start -20 -20) (end 20 -20) (layer "Dwgs.User"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
    )
    without = draw('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))')

    assert with_line.count("viewBox") == without.count("viewBox") == 1
    assert 'viewBox="-20' in with_line
    assert "Dwgs" not in without
    assert "<line" in with_line


def test_mask_and_paste_are_still_skipped():
    """They track the copper and would only thicken the picture."""
    svg = draw(
        '(fp_line (start -9 -9) (end 9 -9) (layer "F.Paste"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
    )

    assert "<line" not in svg
    assert 'viewBox="-9' not in svg

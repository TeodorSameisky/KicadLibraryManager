"""Rewriting a symbol: its properties, and its inheritance."""

from app.kicad.sexpr import SExpr, dumps, loads
from app.kicad.symbol import (
    extends,
    flatten,
    library_document,
    properties,
    replace_property,
    set_property,
)


def units(symbol):
    return [unit.arg(0) for unit in symbol.children("symbol")]


def heads(node):
    return [item.head for item in node.lists()]


# -- properties -------------------------------------------------------------


def test_properties_are_read_by_name():
    symbol = loads('(symbol "R" (property "Value" "10k") (property "Footprint" ""))')
    assert properties(symbol) == {"Value": "10k", "Footprint": ""}


def test_setting_a_property_edits_it_where_it_is():
    """Rebuilt from defaults, the field would move on every part placed."""
    symbol = loads('(symbol "R" (property "Value" "R" (at 1 2 90) (hide yes)))')

    set_property(symbol, "Value", "10k")

    prop = symbol.child("property")
    assert prop.args == ["Value", "10k"]
    assert dumps(prop.child("at")) == "(at 1 2 90)\n"
    assert prop.child("hide") is not None


def test_a_new_property_goes_after_the_others_and_before_the_graphics():
    symbol = loads('(symbol "R" (property "Value" "R") (symbol "R_0_1"))')

    set_property(symbol, "IPN", "1102-0001")

    assert list(properties(symbol)) == ["Value", "IPN"]
    assert symbol.items[-1].head == "symbol", "graphics stay last, as KiCad writes them"


def test_a_new_property_is_hidden():
    """Fields the platform adds belong in the BOM, not on the sheet."""
    symbol = loads('(symbol "R")')

    set_property(symbol, "IPN", "1102-0001")

    prop = symbol.child("property")
    assert prop.child("hide").args == ["yes"]
    assert prop.child("effects") is not None


def test_a_property_with_no_value_gains_one():
    symbol = loads('(symbol "R" (property "Value" (at 0 0 0)))')

    set_property(symbol, "Value", "10k")

    assert properties(symbol) == {"Value": "10k"}
    assert symbol.child("property").child("at") is not None


def test_replacing_a_property_brings_its_layout_with_it():
    symbol = loads('(symbol "R" (property "Value" "R" (at 0 0 0)) (symbol "R_0_1"))')
    theirs = loads('(property "Value" "R_0603" (at 5 5 90))')

    replace_property(symbol, theirs)
    theirs.items[2] = "changed afterwards"

    prop = symbol.child("property")
    assert prop.args == ["Value", "R_0603"], "a copy, not the caller's node"
    assert prop.child("at").args == ["5", "5", "90"]


# -- inheritance ------------------------------------------------------------

PARENT = (
    '(symbol "R"'
    ' (property "Value" "R" (at 0 0 90))'
    ' (property "Footprint" "")'
    ' (symbol "R_0_1" (rectangle (start -1 -2) (end 1 2)))'
    ' (symbol "R_1_1" (pin passive line (at 0 3 270)) (pin passive line (at 0 -3 90)))'
    " (embedded_fonts no))"
)
CHILD = '(symbol "R_0603" (extends "R") (property "Footprint" "Passives:R_0603"))'


def test_a_chain_collapses_into_one_self_contained_symbol():
    """KiCad saves one symbol per payload. One that still said (extends ...)
    would point at a parent that is not in the library it was saved to."""
    flat = flatten([loads(PARENT), loads(CHILD)], "R_0603")

    assert flat.arg(0) == "R_0603"
    assert extends(flat) is None
    assert units(flat), "the parent's graphics came across"


def test_the_descendant_s_properties_win():
    flat = flatten([loads(PARENT), loads(CHILD)], "R_0603")

    assert properties(flat) == {"Value": "R", "Footprint": "Passives:R_0603"}


def test_units_follow_the_rename():
    """They are named <symbol>_<unit>_<style>. Left as the parent's, KiCad
    takes them for another symbol's and draws nothing."""
    flat = flatten([loads(PARENT), loads(CHILD)], "R_0603")
    assert units(flat) == ["R_0603_1_1"]


def test_the_common_unit_is_folded_into_the_real_one():
    """KiCad's remote receive path drops a symbol that uses unit 0: the part
    lands with no graphics. Unit 0 means "common to all units", so folding it
    into each changes nothing -- and every symbol that arrives intact carries
    its body and pins together in _1_1."""
    flat = flatten([loads(PARENT)], "R")
    (unit,) = flat.children("symbol")

    assert unit.arg(0) == "R_1_1"
    assert heads(unit) == ["rectangle", "pin", "pin"], "body first, then the pins"


def test_the_common_unit_reaches_every_unit():
    """A quad op-amp shares one body between four units."""
    node = loads(
        '(symbol "X"'
        ' (symbol "X_0_1" (rectangle (start 0 0) (end 1 1)))'
        ' (symbol "X_1_1" (pin passive line (at 0 1 0)))'
        ' (symbol "X_2_1" (pin passive line (at 0 2 0)))'
        ")"
    )

    flat = flatten([node], "X")

    assert units(flat) == ["X_1_1", "X_2_1"]
    for unit in flat.children("symbol"):
        assert heads(unit) == ["rectangle", "pin"]


def test_a_symbol_drawn_only_in_the_common_unit_gets_a_real_one():
    flat = flatten([loads('(symbol "X" (symbol "X_0_1" (circle (center 0 0) (radius 1))))')], "X")
    assert units(flat) == ["X_1_1"]


def test_each_body_style_keeps_its_own_units():
    """Style 2 is the De Morgan alternative; it must not be merged into 1."""
    node = loads(
        '(symbol "X"'
        ' (symbol "X_0_1" (rectangle (start 0 0) (end 1 1)))'
        ' (symbol "X_0_2" (circle (center 0 0) (radius 1)))'
        ' (symbol "X_1_1" (pin passive line (at 0 1 0)))'
        ' (symbol "X_1_2" (pin passive line (at 0 2 0)))'
        ")"
    )

    flat = flatten([node], "X")
    by_name = {unit.arg(0): heads(unit) for unit in flat.children("symbol")}

    assert by_name == {"X_1_1": ["rectangle", "pin"], "X_1_2": ["circle", "pin"]}


def test_a_symbol_already_in_unit_one_is_left_as_it_is():
    node = loads('(symbol "X" (symbol "X_1_1" (pin passive line (at 0 0 0))))')
    assert dumps(flatten([node], "X")) == dumps(node)


def test_embedded_fonts_stays_last():
    """KiCad writes it after the units. Anywhere else looks hand-edited."""
    flat = flatten([loads(PARENT), loads(CHILD)], "R_0603")
    assert flat.items[-1].head == "embedded_fonts"


def test_flattening_leaves_its_input_alone():
    parent, child = loads(PARENT), loads(CHILD)
    before = dumps(parent), dumps(child)

    flatten([parent, child], "R_0603")

    assert (dumps(parent), dumps(child)) == before


def test_a_symbol_is_wrapped_as_a_complete_file():
    document = library_document(loads('(symbol "R")'))

    assert document.head == "kicad_symbol_lib"
    assert document.child("version").args == ["20251024"]
    assert isinstance(document.items[-1], SExpr) and document.items[-1].head == "symbol"
    assert dumps(loads(dumps(document))) == dumps(document)

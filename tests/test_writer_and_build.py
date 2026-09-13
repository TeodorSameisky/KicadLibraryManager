"""Rendering s-expressions, and building the payload KiCad receives."""

import textwrap

import pytest

from app.kicad.build import BuildError, build_symbol
from app.kicad.catalog import Catalog
from app.kicad.library import index_repository
from app.kicad.parts import load_parts
from app.kicad.sexpr import Atom, SExpr, loads
from app.kicad.writer import dumps, make_property, quote, set_property

from tests.test_library_index import footprint, symbol


# -- writer ---------------------------------------------------------------


def test_round_trips_a_real_kicad_symbol():
    """Our layout must match KiCad's, or every served file looks rewritten."""
    original = (
        "(kicad_symbol_lib\n"
        "\t(version 20251024)\n"
        '\t(generator "kicad_symbol_editor")\n'
        '\t(symbol "R"\n'
        "\t\t(pin_numbers\n\t\t\t(hide yes)\n\t\t)\n"
        '\t\t(property "Value" "R"\n'
        "\t\t\t(at 0 0 90)\n"
        "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )

    assert dumps(loads(original)) == original


def test_numbers_stay_unquoted_and_strings_stay_quoted():
    """Quoting (at 0 0 90) would produce a file KiCad rejects."""
    rendered = dumps(loads('(property "Value" "10k" (at 0 0 90))'))

    assert '"Value"' in rendered
    assert '"10k"' in rendered
    assert "(at 0 0 90)" in rendered


def test_leaf_lists_stay_on_one_line():
    assert dumps(loads("(a (b 1 2) (c (d 3)))")) == "(a\n\t(b 1 2)\n\t(c\n\t\t(d 3)\n\t)\n)\n"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("plain", '"plain"'),
        ('has "quotes"', '"has \\"quotes\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("two\nlines", '"two\\nlines"'),
    ],
)
def test_quoting_escapes(value, expected):
    assert quote(value) == expected


def test_set_property_edits_in_place_keeping_position():
    """Rebuilding it from defaults would move the field on every placed part."""
    sym = loads('(symbol "R" (property "Value" "R" (at 1 2 90) (hide yes)))')
    set_property(sym, "Value", "10k")

    prop = sym.child("property")
    assert prop.atoms() == ["Value", "10k"]
    assert dumps(prop.child("at")) == "(at 1 2 90)\n"
    assert prop.child("hide") is not None


def test_set_property_appends_after_the_last_property():
    sym = loads('(symbol "R" (property "Value" "R") (symbol "R_0_1"))')
    set_property(sym, "IPN", "1102-0001")

    names = [p.atoms()[0] for p in sym.children("property")]
    assert names == ["Value", "IPN"]
    # Graphics must stay last, or the file stops looking like KiCad's own.
    assert isinstance(sym.items[-1], SExpr) and sym.items[-1].head == "symbol"


def test_new_properties_are_hidden():
    prop = make_property("IPN", "1102-0001")
    assert prop.child("hide") is not None, "platform fields belong in the BOM, not the sheet"


def test_atom_compares_equal_to_a_plain_string():
    assert Atom("20251024") == "20251024"


# -- build ----------------------------------------------------------------


PART = """\
ipn: 1102-0001
description: Resistor 10k 1% 0603
status: approved
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
fields:
  Value: 10k
  Tolerance: 1%
mpns:
  - mpn: RC0603FR-0710KL
    preferred: true
"""


class Lib:
    def __init__(self, root):
        self.root = root

    def add(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")

    def build(self, ipn="1102-0001", url="https://kicad.example.com"):
        cat = Catalog()
        cat.add("company", index_repository(self.root))
        cat.check_references()
        parts = load_parts(self.root)
        return build_symbol(parts.parts[ipn], cat, parts, url)


@pytest.fixture()
def lib(tmp_path):
    library = Lib(tmp_path)
    library.add("categories.yaml", '- code: "1102"\n  name: Resistors\n')
    library.add("mpns/RC0603FR-0710KL.yaml",
                "mpn: RC0603FR-0710KL\nmanufacturer: Yageo\nlifecycle: active\n")
    library.add("parts/1102/1102-0001.yaml", PART)
    library.add("symbols/Passives.kicad_symdir/R.kicad_sym", symbol("R", units=2))
    library.add("symbols/Passives.kicad_symdir/R_0603.kicad_sym",
                symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"))
    library.add("footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
                footprint("R_0603_1608Metric"))
    return library


def parse(payload):
    tree = loads(payload.text)
    symbols = list(tree.children("symbol"))
    placed = symbols[-1]
    props = {p.atoms()[0]: (p.atoms()[1] if len(p.atoms()) > 1 else "")
             for p in placed.children("property")}
    return tree, [s.atoms()[0] for s in symbols], props


def test_the_parent_travels_with_the_derived_symbol(lib):
    """Sent alone, a derived symbol places a part with no pins and no body."""
    tree, names, _ = parse(lib.build())

    assert names == ["R", "R_0603"], "ancestors first, placed symbol last"
    parent = list(tree.children("symbol"))[0]
    assert list(parent.children("symbol")), "the parent carries the graphics"


def test_ipn_fields_are_injected(lib):
    _, _, props = parse(lib.build())

    assert props["IPN"] == "1102-0001"
    assert props["Value"] == "10k"
    assert props["Tolerance"] == "1%"
    assert props["Footprint"] == "Passives:R_0603_1608Metric"
    assert props["Description"] == "Resistor 10k 1% 0603"


def test_datasheet_points_at_the_part_page(lib):
    """With several approved sources there is no single manufacturer PDF."""
    _, _, props = parse(lib.build(url="https://kicad.example.com/"))

    assert props["Datasheet"] == "https://kicad.example.com/ipn/1102-0001"


def test_preferred_source_is_named(lib):
    _, _, props = parse(lib.build())

    assert props["MPN"] == "RC0603FR-0710KL"
    assert props["Manufacturer"] == "Yageo"


def test_output_is_parseable_and_stable(lib):
    payload = lib.build()

    assert dumps(loads(payload.text)) == payload.text
    assert payload.filename == "R_0603.kicad_sym"


def test_a_broken_extends_chain_refuses_to_build(lib):
    """Better a clear error than a part that places with no body."""
    lib.add("symbols/Passives.kicad_symdir/R_0603.kicad_sym",
            symbol("R_0603", extends="Missing", footprint="Passives:R_0603_1608Metric"))

    with pytest.raises(BuildError, match="no body"):
        lib.build()


def test_an_unknown_symbol_refuses_to_build(lib):
    lib.add("parts/1102/1102-0001.yaml", PART.replace("Passives:R_0603", "Passives:Ghost"))

    with pytest.raises(BuildError, match="no source provides"):
        lib.build()


def test_a_part_without_a_symbol_refuses_to_build(lib):
    lib.add("parts/1102/1102-0001.yaml", PART.replace("symbol: Passives:R_0603", "symbol: ''"))

    with pytest.raises(BuildError, match="no usable symbol"):
        lib.build()


def test_a_self_contained_symbol_needs_no_ancestors(lib):
    lib.add("parts/1102/1102-0001.yaml", PART.replace("Passives:R_0603", "Passives:R"))

    _, names, props = parse(lib.build())

    assert names == ["R"]
    assert props["IPN"] == "1102-0001"


def test_coordinate_lists_are_packed_and_wrapped():
    """KiCad shares a line between points and wraps near column 120."""
    pts = " ".join(f"(xy {i}.0254 {i}.3048)" for i in range(12))
    rendered = dumps(loads(f"(polyline (pts {pts}))"))
    point_lines = [ln for ln in rendered.splitlines() if "(xy " in ln]

    assert len(point_lines) > 1, "a long point list wraps"
    assert point_lines[0].count("(xy ") > 1, "points share a line"
    # The remainder may well be a single point; what matters is the wrap column.
    assert max(len(ln) for ln in point_lines) < 130
    assert sum(ln.count("(xy ") for ln in point_lines) == 12, "no point is lost"


def test_a_short_point_list_stays_on_one_line():
    rendered = dumps(loads("(polyline (pts (xy 0 0) (xy 1 1)))"))
    assert "(xy 0 0) (xy 1 1)" in rendered

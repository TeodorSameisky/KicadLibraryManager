"""Cross-source resolution.

The case that motivates all of this: KiCad's own libraries keep symbols,
footprints and 3D models in three separate repositories, so a symbol's
footprint reference resolves only when every source is considered together.
"""

import pytest

from app.kicad.catalog import Catalog
from app.kicad.library import index_repository

from tests.test_library_index import footprint, symbol


class Src:
    """One source repository on disk."""

    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def add(self, relative, text):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@pytest.fixture()
def make_source(tmp_path):
    def _make(name):
        return Src(tmp_path / name)
    return _make


def build(*pairs) -> Catalog:
    cat = Catalog()
    for source_id, src in pairs:
        cat.add(source_id, index_repository(src.root))
    cat.check_references()
    return cat


def test_footprint_resolves_from_another_source(make_source):
    syms = make_source("symbols-repo")
    syms.add("Device.kicad_symdir/R.kicad_sym",
             symbol("R", footprint="Resistor_SMD:R_0603_1608Metric"))

    fps = make_source("footprints-repo")
    fps.add("Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod",
            footprint("R_0603_1608Metric"))

    cat = build(("symbols", syms), ("footprints", fps))

    assert cat.errors == [], "a reference across sources must resolve"
    assert len(cat.find_footprint("Resistor_SMD", "R_0603_1608Metric")) == 1


def test_footprint_missing_from_every_source_is_an_error(make_source):
    syms = make_source("symbols-repo")
    syms.add("Device.kicad_symdir/R.kicad_sym", symbol("R", footprint="Nope:Absent"))

    cat = build(("symbols", syms))

    assert [i.kind for i in cat.errors] == ["missing-footprint"]
    assert "no source provides" in cat.errors[0].message


def test_model_resolves_from_a_third_source(make_source):
    fps = make_source("footprints-repo")
    fps.add("Resistor_SMD.pretty/R_0603.kicad_mod",
            footprint("R_0603",
                      model="${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step"))

    models = make_source("packages3d-repo")
    models.add("Resistor_SMD.3dshapes/R_0603.step", "ISO-10303-21;\n")

    cat = build(("footprints", fps), ("models", models))

    assert cat.errors == []
    assert len(cat.find_model("Resistor_SMD.3dshapes/R_0603.step")) == 1


def test_the_same_footprint_in_two_sources_is_flagged(make_source):
    """Silently picking one would make placements depend on source ordering."""
    syms = make_source("symbols-repo")
    syms.add("Device.kicad_symdir/R.kicad_sym", symbol("R", footprint="Shared:R_0603"))

    a = make_source("a")
    a.add("Shared.pretty/R_0603.kicad_mod", footprint("R_0603"))
    b = make_source("b")
    b.add("Shared.pretty/R_0603.kicad_mod", footprint("R_0603"))

    cat = build(("symbols", syms), ("a", a), ("b", b))

    assert [i.kind for i in cat.warnings] == ["ambiguous-footprint"]
    assert cat.errors == []
    assert len(cat.find_footprint("Shared", "R_0603")) == 2


def test_per_source_issues_are_included(make_source):
    src = make_source("lib")
    src.add("L.kicad_symdir/B.kicad_sym", symbol("B", extends="Gone"))

    cat = build(("lib", src))

    assert any(i.kind == "missing-parent" for i in cat.errors)


def test_issues_name_the_source(make_source):
    src = make_source("lib")
    src.add("L.kicad_symdir/R.kicad_sym", symbol("R", footprint="X:Y"))

    cat = build(("company", src))

    assert "[company]" in cat.errors[0].message


def test_rechecking_does_not_duplicate_issues(make_source):
    src = make_source("lib")
    src.add("L.kicad_symdir/R.kicad_sym", symbol("R", footprint="X:Y"))

    cat = build(("lib", src))
    first = len(cat.errors)
    cat.check_references()

    assert len(cat.errors) == first


def test_summary_counts_sources_and_symbols(make_source):
    a = make_source("a")
    a.add("L.kicad_symdir/R.kicad_sym", symbol("R", units=2))
    a.add("L.kicad_symdir/R_0603.kicad_sym", symbol("R_0603", extends="R"))

    cat = build(("a", a))
    text = cat.summary()

    assert cat.symbol_count() == 2
    assert "1 source(s), 2 symbol(s)" in text
    assert "(1 derived)" in text

"""Loading IPNs and MPNs, and validating them against the asset catalog."""

import textwrap

import pytest

from app.kicad.catalog import Catalog
from app.kicad.library import index_repository
from app.kicad.parts import check_against_catalog, load_parts

from tests.test_library_index import footprint, symbol

CATEGORIES = """\
- code: "1101"
  name: Capacitors, fixed
- code: "1102"
  name: Resistors, fixed
"""

MPN_YAGEO = """\
mpn: RC0603FR-0710KL
manufacturer: Yageo
datasheet: https://example.com/yageo.pdf
lifecycle: active
"""

PART = """\
ipn: 1102-0001
description: Resistor 10k 1% 0.1W 0603
status: approved
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
fields:
  Value: 10k
mpns:
  - mpn: RC0603FR-0710KL
    preferred: true
"""


class Lib:
    def __init__(self, root):
        self.root = root

    def add(self, relative, text):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")

    def load(self):
        return load_parts(self.root)

    def complete(self):
        """A library where everything resolves."""
        self.add("categories.yaml", CATEGORIES)
        self.add("mpns/RC0603FR-0710KL.yaml", MPN_YAGEO)
        self.add("parts/1102/1102-0001.yaml", PART)
        self.add("symbols/Passives.kicad_symdir/R.kicad_sym", symbol("R", units=2))
        self.add("symbols/Passives.kicad_symdir/R_0603.kicad_sym",
                 symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"))
        self.add("footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
                 footprint("R_0603_1608Metric"))
        return self


@pytest.fixture()
def lib(tmp_path):
    return Lib(tmp_path)


def resolved(lib):
    """Load parts and check them against a catalog built from the same tree."""
    index = lib.load()
    cat = Catalog()
    cat.add("company", index_repository(lib.root))
    cat.check_references()
    check_against_catalog(index, cat)
    return index


def test_loads_a_complete_library(lib):
    index = resolved(lib.complete())

    assert index.errors == []
    part = index.parts["1102-0001"]
    assert part.category == "1102"
    assert part.fields == {"Value": "10k"}
    assert part.preferred_mpn == "RC0603FR-0710KL"
    assert index.mpns["RC0603FR-0710KL"].manufacturer == "Yageo"


def test_where_used_reverse_index(lib):
    """The question asked when a manufacturer discontinues something."""
    lib.complete()
    lib.add("parts/1102/1102-0002.yaml", PART.replace("1102-0001", "1102-0002"))

    index = lib.load()

    assert [p.ipn for p in index.parts_using("RC0603FR-0710KL")] == ["1102-0001", "1102-0002"]
    assert index.parts_using("NOT-USED") == []


def test_unknown_mpn_reference_is_an_error(lib):
    lib.complete()
    lib.add("parts/1102/1102-0003.yaml", PART.replace(
        "1102-0001", "1102-0003").replace("RC0603FR-0710KL", "GHOST-PART"))

    index = lib.load()

    assert any(i.kind == "unknown-mpn" for i in index.errors)


def test_ipn_must_match_its_filename(lib):
    lib.complete()
    lib.add("parts/1102/1102-0009.yaml", PART)  # declares 1102-0001

    index = lib.load()

    assert any(i.kind == "name-mismatch" for i in index.errors)


def test_undeclared_category_is_rejected(lib):
    lib.complete()
    lib.add("parts/9999/9999-0001.yaml", PART.replace("1102-0001", "9999-0001"))

    index = lib.load()

    assert any(i.kind == "unknown-category" for i in index.errors)


def test_malformed_ipn_is_rejected(lib):
    lib.complete()
    lib.add("parts/1102/not-an-ipn.yaml", PART.replace("1102-0001", "not-an-ipn"))

    index = lib.load()

    assert any(i.kind == "bad-ipn" for i in index.errors)


def test_part_filed_under_the_wrong_category_is_flagged(lib):
    lib.complete()
    lib.add("parts/1101/1102-0004.yaml", PART.replace("1102-0001", "1102-0004"))

    index = lib.load()

    assert any(i.kind == "misfiled" for i in index.warnings)


def test_two_preferred_mpns_is_an_error(lib):
    lib.complete()
    lib.add("mpns/OTHER-MPN.yaml", MPN_YAGEO.replace("RC0603FR-0710KL", "OTHER-MPN"))
    lib.add("parts/1102/1102-0005.yaml", """\
        ipn: 1102-0005
        description: Resistor
        symbol: Passives:R_0603
        mpns:
          - mpn: RC0603FR-0710KL
            preferred: true
          - mpn: OTHER-MPN
            preferred: true
        """)

    index = lib.load()

    assert any(i.kind == "multiple-preferred" for i in index.errors)


def test_approved_part_with_an_obsolete_source_is_flagged(lib):
    lib.complete()
    lib.add("mpns/OLD-PART.yaml", """\
        mpn: OLD-PART
        manufacturer: Acme
        lifecycle: obsolete
        """)
    lib.add("parts/1102/1102-0006.yaml", """\
        ipn: 1102-0006
        description: Resistor
        status: approved
        symbol: Passives:R_0603
        mpns:
          - mpn: OLD-PART
        """)

    index = lib.load()

    assert any(i.kind == "obsolete-mpn" for i in index.warnings)


def test_missing_description_is_a_warning(lib):
    """An opaque number with no description cannot be found by anyone."""
    lib.complete()
    lib.add("parts/1102/1102-0007.yaml", """\
        ipn: 1102-0007
        symbol: Passives:R_0603
        mpns:
          - mpn: RC0603FR-0710KL
        """)

    index = lib.load()

    assert any(i.kind == "no-description" for i in index.warnings)


def test_approved_part_without_any_source_is_flagged(lib):
    lib.complete()
    lib.add("parts/1102/1102-0008.yaml", """\
        ipn: 1102-0008
        description: Resistor
        status: approved
        symbol: Passives:R_0603
        """)

    index = lib.load()

    assert any(i.kind == "no-mpns" for i in index.warnings)


def test_symbol_and_footprint_are_resolved_through_the_catalog(lib):
    lib.complete()
    lib.add("parts/1101/1101-0001.yaml", """\
        ipn: 1101-0001
        description: Capacitor
        symbol: Passives:Missing
        footprint: Passives:AlsoMissing
        """)

    index = resolved(lib)
    kinds = {i.kind for i in index.errors}

    assert "missing-symbol" in kinds
    assert "missing-footprint" in kinds


def test_invalid_yaml_does_not_stop_the_walk(lib):
    lib.complete()
    lib.add("parts/1102/broken.yaml", "ipn: [unclosed\n")

    index = lib.load()

    assert "1102-0001" in index.parts
    assert any(i.kind == "invalid-yaml" for i in index.errors)


def test_duplicate_ipn_is_reported(lib):
    lib.complete()
    lib.add("parts/1101/1102-0001.yaml", PART)

    index = lib.load()

    assert any(i.kind == "duplicate-ipn" for i in index.errors)


def test_mpns_may_be_plain_strings(lib):
    """The shorthand form, for a part with a single source."""
    lib.complete()
    lib.add("parts/1102/1102-0010.yaml", """\
        ipn: 1102-0010
        description: Resistor
        symbol: Passives:R_0603
        mpns:
          - RC0603FR-0710KL
        """)

    index = lib.load()

    part = index.parts["1102-0010"]
    assert part.preferred_mpn == "RC0603FR-0710KL", "a lone source is the preferred one"
    assert not part.mpns[0].preferred


def test_missing_categories_file_is_a_warning_not_a_failure(lib):
    lib.add("mpns/RC0603FR-0710KL.yaml", MPN_YAGEO)
    lib.add("parts/1102/1102-0001.yaml", PART)

    index = lib.load()

    assert any(i.kind == "no-categories" for i in index.warnings)
    assert "1102-0001" in index.parts, "parts still load without the registry"

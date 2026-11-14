"""Indexing one repository's symbols, footprints and 3D models."""

import pytest

from app.library.assets import BrokenInheritance, index_repository
from app.library.issues import Issue, Severity, errors, warnings
from app.library.refs import LibraryRef, model_key
from tests.support import footprint_file, symbol_file


def kinds(issues):
    return [issue.kind for issue in issues]


def test_symbols_footprints_and_models_are_indexed(tree):
    index = index_repository(tree.standard(model=True).root)

    assert set(index.symbols) == {("Passives", "R"), ("Passives", "R_0603")}
    assert set(index.footprints) == {("Passives", "R_0603_1608Metric")}
    assert set(index.models) == {"Passives.3dshapes/R_0603_1608Metric.step"}
    assert index.issues == []


def test_what_a_symbol_and_a_footprint_refer_to_is_recorded(tree):
    index = index_repository(tree.standard(model=True).root)

    symbol = index.symbols[("Passives", "R_0603")]
    assert symbol.extends == "R"
    assert symbol.footprint == "Passives:R_0603_1608Metric"

    (reference,) = index.footprints[("Passives", "R_0603_1608Metric")].model_refs
    assert reference.startswith("${KICAD_LIBRARY_3DMODELS}/"), "kept as the file has it"


def test_libraries_at_the_repository_root_are_read_too(tree):
    """KiCad's own libraries are one repository per kind of asset, with the
    library directories at the top."""
    tree.write("Device.kicad_symdir/R.kicad_sym", symbol_file("R"))
    tree.write("Resistor_SMD.pretty/R_0603.kicad_mod", footprint_file("R_0603"))
    tree.write("Resistor_SMD.3dshapes/R_0603.step", "ISO-10303-21;\n")

    index = index_repository(tree.root)

    assert ("Device", "R") in index.symbols
    assert ("Resistor_SMD", "R_0603") in index.footprints
    assert "Resistor_SMD.3dshapes/R_0603.step" in index.models


# -- inheritance ------------------------------------------------------------


def test_ancestry_runs_from_the_symbol_to_its_root(tree):
    tree.write("symbols/L.kicad_symdir/A.kicad_sym", symbol_file("A", units=2))
    tree.write("symbols/L.kicad_symdir/B.kicad_sym", symbol_file("B", extends="A"))
    tree.write("symbols/L.kicad_symdir/C.kicad_sym", symbol_file("C", extends="B"))

    index = index_repository(tree.root)
    chain = index.ancestry(index.symbols[("L", "C")])

    assert [symbol.name for symbol in chain] == ["C", "B", "A"]
    assert index.issues == []


def test_a_missing_parent_is_an_error(tree):
    """A derived symbol has no graphics of its own: without its parent the
    part places with no body and no pins."""
    tree.write("symbols/L.kicad_symdir/B.kicad_sym", symbol_file("B", extends="Gone"))

    index = index_repository(tree.root)

    (issue,) = index.issues
    assert (issue.severity, issue.kind) == (Severity.ERROR, "broken-inheritance")
    assert "'B' extends 'Gone', which is not in L" in issue.message
    with pytest.raises(BrokenInheritance):
        index.ancestry(index.symbols[("L", "B")])


def test_a_cycle_is_an_error_and_not_an_endless_loop(tree):
    tree.write("symbols/L.kicad_symdir/A.kicad_sym", symbol_file("A", extends="B"))
    tree.write("symbols/L.kicad_symdir/B.kicad_sym", symbol_file("B", extends="A"))

    index = index_repository(tree.root)

    assert kinds(index.issues) == ["broken-inheritance", "broken-inheritance"]
    assert "cycle in L: A -> B -> A" in index.issues[0].message


# -- what is wrong with a file ----------------------------------------------


def test_a_file_that_does_not_parse_does_not_stop_the_walk(tree):
    tree.write("symbols/L.kicad_symdir/broken.kicad_sym", "(kicad_symbol_lib")
    tree.write("symbols/L.kicad_symdir/R.kicad_sym", symbol_file("R"))

    index = index_repository(tree.root)

    assert ("L", "R") in index.symbols
    (issue,) = index.issues
    assert issue.kind == "unparseable"
    assert issue.path == "symbols/L.kicad_symdir/broken.kicad_sym", "relative to the repository"


def test_a_file_holding_something_else_is_reported(tree):
    tree.write("symbols/L.kicad_symdir/R.kicad_sym", footprint_file("R"))
    tree.write("footprints/L.pretty/F.kicad_mod", symbol_file("F"))

    assert kinds(index_repository(tree.root).issues) == ["unexpected-content"] * 2


def test_a_symbol_file_with_no_symbol_is_reported(tree):
    tree.write("symbols/L.kicad_symdir/R.kicad_sym", "(kicad_symbol_lib (version 20251024))")
    assert kinds(index_repository(tree.root).issues) == ["empty"]


def test_a_name_that_differs_from_its_file_is_a_warning(tree):
    tree.write("symbols/L.kicad_symdir/Wrong.kicad_sym", symbol_file("R"))
    tree.write("footprints/L.pretty/Wrong.kicad_mod", footprint_file("F"))

    index = index_repository(tree.root)

    assert kinds(warnings(index.issues)) == ["name-mismatch", "name-mismatch"]
    assert ("L", "R") in index.symbols, "indexed under the name it really has"
    assert ("L", "F") in index.footprints


def test_the_same_name_twice_is_an_error(tree):
    tree.write("symbols/L.kicad_symdir/R.kicad_sym", symbol_file("R"))
    tree.write("symbols/L.kicad_symdir/R2.kicad_sym", symbol_file("R"))
    tree.write("footprints/L.pretty/F.kicad_mod", footprint_file("F"))
    tree.write("footprints/L.pretty/F2.kicad_mod", footprint_file("F"))

    assert kinds(errors(index_repository(tree.root).issues)) == [
        "duplicate-symbol",
        "duplicate-footprint",
    ]


def test_a_footprint_with_no_library_is_a_warning(tree):
    tree.write("symbols/L.kicad_symdir/R.kicad_sym", symbol_file("R", footprint="R_0603"))

    index = index_repository(tree.root)

    assert kinds(index.issues) == ["unqualified-footprint"]
    assert errors(index.issues) == []


def test_whether_a_footprint_or_model_exists_is_not_decided_here(tree):
    """A reference that dangles in this repository may resolve against
    another source, and only the catalog sees them all."""
    tree.write("symbols/L.kicad_symdir/R.kicad_sym", symbol_file("R", footprint="Other:Nope"))
    tree.write(
        "footprints/L.pretty/F.kicad_mod",
        footprint_file("F", model="${X}/Other.3dshapes/absent.step"),
    )

    assert index_repository(tree.root).issues == []


def test_an_lfs_pointer_is_an_error(tree):
    """Sent to KiCad, the stub gives an empty 3D view and no error anywhere."""
    tree.write(
        "3dmodels/L.3dshapes/R.step",
        "version https://git-lfs.github.com/spec/v1\noid sha256:4d7a2\nsize 41234\n",
    )

    index = index_repository(tree.root)

    assert index.models["L.3dshapes/R.step"].is_lfs_pointer
    assert kinds(errors(index.issues)) == ["lfs-pointer"]


def test_only_model_files_are_models(tree):
    tree.write("3dmodels/L.3dshapes/R.step", "ISO-10303-21;\n")
    tree.write("3dmodels/L.3dshapes/R.STP", "ISO-10303-21;\n")
    tree.write("3dmodels/L.3dshapes/notes.txt", "not a model")

    assert set(index_repository(tree.root).models) == {"L.3dshapes/R.step", "L.3dshapes/R.STP"}


# -- references -------------------------------------------------------------


def test_a_reference_splits_on_its_first_colon():
    ref = LibraryRef.parse("Passives:R_0603_1608Metric")

    assert ref == LibraryRef("Passives", "R_0603_1608Metric")
    assert str(ref) == "Passives:R_0603_1608Metric"
    assert LibraryRef.parse("Lib:A:B") == LibraryRef("Lib", "A:B")


@pytest.mark.parametrize(
    "raw",
    ["", None, "R_0603", "Passives:", ":R_0603", ":"],
    ids=["empty", "none", "unqualified", "no-name", "no-library", "colon-only"],
)
def test_a_reference_that_names_nothing_is_not_one(raw):
    """Resolving it would report an asset no source provides, and send the
    reader looking for a missing file rather than at the typo."""
    assert LibraryRef.parse(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "${KICAD10_3DMODEL_DIR}/L.3dshapes/R.step",
        "${KIPRJMOD}/L.3dshapes/R.step",
        "${ANY_VARIABLE_AT_ALL}/L.3dshapes/R.step",
        "L.3dshapes/R.step",
        "./L.3dshapes/R.step",
        "/L.3dshapes/R.step",
        r"${VAR}\L.3dshapes\R.step",
        "  ${VAR}/L.3dshapes/R.step  ",
    ],
)
def test_a_model_reference_is_reduced_to_its_place_in_a_repository(raw):
    """The variable is each contributor's own. Only what follows it means
    anything on the server."""
    assert model_key(raw) == "L.3dshapes/R.step"


def test_an_issue_reads_as_a_sentence_with_its_place():
    issue = Issue.error("missing-parent", "'B' extends 'Gone'", "symbols/B.kicad_sym")

    assert str(issue) == "error: 'B' extends 'Gone' [symbols/B.kicad_sym]"
    assert str(issue.from_source("company")).endswith("[company/symbols/B.kicad_sym]")
    assert str(Issue.warning("no-categories", "categories.yaml is missing")) == (
        "warning: categories.yaml is missing"
    )

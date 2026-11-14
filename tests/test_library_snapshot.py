"""Several sources indexed together, and the questions asked of the result.

What motivates the first half: KiCad's own libraries keep symbols, footprints
and 3D models in three separate repositories, so a symbol's footprint only
resolves when every source is looked at together.
"""

import pytest

from app.library.assets import BrokenInheritance
from app.library.refs import LibraryRef
from app.library.snapshot import Snapshot, SyncFailure, build_snapshot
from app.library.sources import Source
from tests.support import PART, LibraryTree, footprint_file, symbol_file


@pytest.fixture()
def source(tmp_path):
    """Make another repository, as its own source."""

    def make(source_id: str) -> LibraryTree:
        return LibraryTree(tmp_path / source_id, Source(source_id, source_id, "unused"))

    return make


def index(*trees: LibraryTree) -> Snapshot:
    return build_snapshot([tree.checkout for tree in trees])


def kinds(issues):
    return [issue.kind for issue in issues]


# -- references across sources ----------------------------------------------


def test_a_footprint_resolves_from_another_source(source):
    symbols, footprints = source("symbols"), source("footprints")
    symbols.write("Device.kicad_symdir/R.kicad_sym", symbol_file("R", footprint="SMD:R_0603"))
    footprints.write("SMD.pretty/R_0603.kicad_mod", footprint_file("R_0603"))

    snapshot = index(symbols, footprints)

    assert snapshot.issues == ()
    assert snapshot.catalog.footprint(LibraryRef("SMD", "R_0603")).name == "R_0603"


def test_a_model_resolves_from_a_third(source):
    footprints, models = source("footprints"), source("models")
    footprints.write(
        "SMD.pretty/R_0603.kicad_mod",
        footprint_file("R_0603", model="${KICAD10_3DMODEL_DIR}/SMD.3dshapes/R_0603.step"),
    )
    models.write("SMD.3dshapes/R_0603.step", "ISO-10303-21;\n")

    snapshot = index(footprints, models)

    assert snapshot.issues == ()
    assert snapshot.catalog.model("SMD.3dshapes/R_0603.step") is not None


def test_a_footprint_no_source_has_is_an_error(source):
    symbols = source("symbols")
    symbols.write("Device.kicad_symdir/R.kicad_sym", symbol_file("R", footprint="Nope:Absent"))

    (issue,) = index(symbols).issues

    assert issue.kind == "missing-footprint"
    assert issue.source == "symbols", "which repository to open"
    assert issue.path == "Device.kicad_symdir/R.kicad_sym"
    assert "which no source provides" in issue.message


def test_a_model_no_source_has_is_an_error(source):
    footprints = source("footprints")
    footprints.write(
        "SMD.pretty/R.kicad_mod", footprint_file("R", model="${X}/SMD.3dshapes/gone.step")
    )

    assert kinds(index(footprints).errors) == ["missing-model"]


def test_the_same_footprint_in_two_sources_is_flagged(source):
    """Picking one silently would make what gets placed depend on the order
    the sources happen to be listed in."""
    symbols, first, second = source("symbols"), source("first"), source("second")
    symbols.write("Device.kicad_symdir/R.kicad_sym", symbol_file("R", footprint="Shared:R"))
    first.write("Shared.pretty/R.kicad_mod", footprint_file("R"))
    second.write("Shared.pretty/R.kicad_mod", footprint_file("R"))

    snapshot = index(symbols, first, second)

    (issue,) = snapshot.issues
    assert issue.kind == "ambiguous-footprint"
    assert "first, second" in issue.message
    assert snapshot.errors == []
    assert snapshot.catalog.footprint(LibraryRef("Shared", "R")).path.parts[-3] == "first"


def test_each_repository_s_own_issues_are_carried_along(source):
    library = source("company")
    library.write("L.kicad_symdir/B.kicad_sym", symbol_file("B", extends="Gone"))

    (issue,) = index(library).errors

    assert (issue.kind, issue.source) == ("broken-inheritance", "company")


def test_ancestry_is_resolved_in_the_source_the_symbol_came_from(source):
    library = source("company")
    library.write("L.kicad_symdir/A.kicad_sym", symbol_file("A", units=2))
    library.write("L.kicad_symdir/B.kicad_sym", symbol_file("B", extends="A"))
    library.write("L.kicad_symdir/C.kicad_sym", symbol_file("C", extends="Gone"))
    catalog = index(library).catalog

    assert [s.name for s in catalog.symbol_ancestry(LibraryRef("L", "B"))] == ["B", "A"]
    with pytest.raises(LookupError):
        catalog.symbol_ancestry(LibraryRef("L", "Nope"))
    with pytest.raises(BrokenInheritance):
        catalog.symbol_ancestry(LibraryRef("L", "C"))


# -- parts against the assets -----------------------------------------------


def test_a_part_s_symbol_and_footprint_are_resolved(tree):
    tree.standard().part(
        "1101-0001",
        """\
        ipn: 1101-0001
        description: Capacitor
        symbol: Passives:Missing
        footprint: Passives:AlsoMissing
        """,
    )

    problems = [i for i in tree.snapshot().errors if i.path == "parts/1101/1101-0001.yaml"]

    assert kinds(problems) == ["missing-symbol", "missing-footprint"]


def test_a_part_naming_an_asset_without_a_library_is_an_error(tree):
    tree.standard().part(
        "1101-0001",
        "ipn: 1101-0001\ndescription: Capacitor\nsymbol: R_0603\nfootprint: R_0603_1608Metric\n",
    )

    assert {"unqualified-symbol", "unqualified-footprint"} <= set(kinds(tree.snapshot().errors))


def test_a_part_whose_symbol_has_no_body_is_called_out(tree):
    """The symbol's own error says the chain is broken. This one says which
    part cannot be placed because of it."""
    tree.standard().write(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol_file("R_0603", extends="Gone", footprint="Passives:R_0603_1608Metric"),
    )

    assert kinds(tree.snapshot().errors) == ["broken-inheritance", "unplaceable"]


def test_parts_come_from_the_first_source_that_has_any(source):
    assets, first, second = source("assets"), source("first"), source("second")
    assets.standard()
    (assets.root / "parts" / "1102" / "1102-0001.yaml").unlink()
    (assets.root / "parts" / "1102").rmdir()
    (assets.root / "parts").rmdir()
    first.part("1102-0001")
    second.part("1102-0002")

    snapshot = index(assets, first, second)

    assert list(snapshot.parts.parts) == ["1102-0001"]
    assert [s.parts for s in snapshot.sources] == [0, 1, 0]
    ignored = [i for i in snapshot.warnings if i.kind == "parts-ignored"]
    assert [i.source for i in ignored] == ["second"], "and says so, rather than dropping them"


# -- sources ----------------------------------------------------------------


def test_each_source_reports_what_it_holds(tree):
    snapshot = tree.standard(model=True).snapshot()

    (status,) = snapshot.sources
    assert (status.id, status.name, status.commit) == ("company", "Company Library", "abc1234def")
    assert (status.symbols, status.footprints, status.models, status.parts) == (2, 1, 1, 1)
    assert status.error is None
    assert not snapshot.failed


def test_a_source_that_did_not_sync_is_listed_with_its_error(tree):
    broken = Source("vendor", "Vendor", "unused")

    snapshot = build_snapshot([tree.standard().checkout, SyncFailure(broken, "git clone failed")])

    assert [s.id for s in snapshot.sources] == ["company", "vendor"]
    assert snapshot.sources[1].error == "git clone failed"
    assert snapshot.part_count == 1
    assert not snapshot.failed, "one source is enough to serve"
    assert build_snapshot([SyncFailure(broken, "git clone failed")]).failed


def test_an_empty_snapshot_answers_everything(tree):
    """What is served while the first index is still running."""
    snapshot = Snapshot()

    assert snapshot.part_count == 0
    assert snapshot.part("1102-0001") is None
    assert snapshot.search("anything") == []
    assert snapshot.status_facets == [] and snapshot.category_facets == []
    assert snapshot.errors == [] and snapshot.indexed_at is None


# -- questions --------------------------------------------------------------


@pytest.fixture()
def snapshot(tree):
    tree.standard()
    tree.part("1102-0002", PART.replace("10k", "4k7").replace("approved", "draft"))
    tree.part(
        "1101-0001",
        "ipn: 1101-0001\ndescription: Capacitor 100n X7R\nsymbol: Passives:R\nfields:\n  Value: 100n\n",
    )
    return tree.snapshot()


def found(parts):
    return [part.ipn for part in parts]


def test_search_with_nothing_asked_returns_everything_by_number(snapshot):
    assert found(snapshot.search()) == ["1101-0001", "1102-0001", "1102-0002"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("4k7", ["1102-0002"]),  # a field value
        ("capacitor", ["1101-0001"]),  # the description, whatever the case
        ("RC0603", ["1102-0001", "1102-0002"]),  # a manufacturer part
        ("yageo", ["1102-0001", "1102-0002"]),  # its manufacturer
        ("1102-", ["1102-0001", "1102-0002"]),  # the number itself
        ("  100n  ", ["1101-0001"]),  # padded
        ("nonexistent", []),
    ],
)
def test_search_matches_what_people_know_about_a_part(snapshot, query, expected):
    """The numbers mean nothing, so matching only those would be useless."""
    assert found(snapshot.search(query)) == expected


def test_search_narrows_by_status_and_category(snapshot):
    assert found(snapshot.search(status="draft")) == ["1101-0001", "1102-0002"]
    assert found(snapshot.search(category="1102")) == ["1102-0001", "1102-0002"]
    assert found(snapshot.search("resistor", status="draft", category="1102")) == ["1102-0002"]


def test_filters_are_offered_with_their_counts(snapshot):
    statuses = [(f.value, f.count) for f in snapshot.status_facets]
    categories = [(f.value, f.label, f.count) for f in snapshot.category_facets]

    assert statuses == [("draft", 2), ("approved", 1)], "in lifecycle order, none empty"
    # The label is the name from categories.yaml. The code alone means nothing.
    assert categories == [("1101", "Capacitors, fixed", 1), ("1102", "Resistors, fixed", 2)]


def test_parts_sharing_a_source_are_related(snapshot):
    """How far a discontinuation reaches."""
    assert found(snapshot.related(snapshot.part("1102-0001"))) == ["1102-0002"]
    assert snapshot.related(snapshot.part("1101-0001")) == []


def test_a_category_without_a_name_is_shown_by_its_code(snapshot):
    assert snapshot.category_name("1102") == "Resistors, fixed"
    assert snapshot.category_name("7777") == "7777"

"""Catalog endpoints and the IPN page."""

import pytest

from app.kicad.catalog import Catalog
from app.kicad.library import index_repository
from app.kicad.parts import check_against_catalog, load_parts
from app.library_service import LibraryService, Snapshot, SourceStatus, State
from tests.test_library_index import footprint, symbol
from tests.test_parts import CATEGORIES, MPN_YAGEO, PART


@pytest.fixture()
def library(tmp_path):
    """A ready service backed by a directory, with no git involved."""

    def write(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    write("categories.yaml", CATEGORIES)
    write("mpns/RC0603FR-0710KL.yaml", MPN_YAGEO)
    write("parts/1102/1102-0001.yaml", PART)
    write("symbols/Passives.kicad_symdir/R.kicad_sym", symbol("R", units=2))
    write(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"),
    )
    write("footprints/Passives.pretty/R_0603_1608Metric.kicad_mod", footprint("R_0603_1608Metric"))

    catalog = Catalog()
    catalog.add("company", index_repository(tmp_path))
    catalog.check_references()
    parts = load_parts(tmp_path)
    check_against_catalog(parts, catalog)

    snapshot = Snapshot(
        catalog=catalog,
        parts=parts,
        sources=[SourceStatus(id="company", name="Company", commit="abc123", symbols=2, parts=1)],
        finished_at=1.0,
        duration=0.2,
    )
    service = LibraryService(sources=[], workdir=tmp_path)
    service._snapshot = snapshot
    service._state = State.READY
    return service


@pytest.fixture()
def ready(signed_in_client, library):
    signed_in_client.app.state.library = library
    return signed_in_client


def test_status_reports_a_ready_index(ready):
    body = ready.get("/api/v1/status").json()

    assert body["state"] == "ready"
    assert body["parts"] == 1
    assert body["symbols"] == 2
    assert body["errors"] == 0
    assert body["sources"][0]["commit"] == "abc123"


def test_list_parts(ready):
    body = ready.get("/api/v1/parts").json()

    assert body["total"] == 1
    part = body["parts"][0]
    assert part["ipn"] == "1102-0001"
    assert part["preferred_mpn"] == "RC0603FR-0710KL"
    assert part["manufacturer"] == "Yageo"


@pytest.mark.parametrize("query", ["10k", "yageo", "1102", "resistor"])
def test_search_finds_the_part(ready, query):
    body = ready.get(f"/api/v1/parts?q={query}").json()
    assert [p["ipn"] for p in body["parts"]] == ["1102-0001"]


def test_search_that_matches_nothing(ready):
    body = ready.get("/api/v1/parts?q=zzzz").json()
    assert body["parts"] == []
    assert body["total"] == 1, "total still reports the catalog size"


def test_part_detail_includes_sources(ready):
    body = ready.get("/api/v1/parts/1102-0001").json()

    assert body["symbol"] == "Passives:R_0603"
    assert body["fields"] == {"Value": "10k"}
    assert body["mpns"][0] == {
        "mpn": "RC0603FR-0710KL",
        "preferred": True,
        "manufacturer": "Yageo",
        "datasheet": "https://example.com/yageo.pdf",
        "lifecycle": "active",
        "known": True,
    }


def test_unknown_part_is_404(ready):
    assert ready.get("/api/v1/parts/9999-9999").status_code == 404
    assert ready.get("/ipn/9999-9999").status_code == 404


def test_ipn_page_renders(ready):
    """This URL is what the Datasheet field in every placed symbol points to."""
    html = ready.get("/ipn/1102-0001").text

    assert "1102-0001" in html
    assert "Resistor 10k 1% 0.1W 0603" in html
    assert "RC0603FR-0710KL" in html
    assert "Yageo" in html
    assert "https://example.com/yageo.pdf" in html
    assert "preferred" in html


def test_endpoints_are_usable_before_the_first_index(signed_in_client):
    """The app must answer while the initial clone is still running."""
    service = LibraryService(sources=[], workdir=None)
    service._state = State.SYNCING
    signed_in_client.app.state.library = service

    status = signed_in_client.get("/api/v1/status").json()
    parts = signed_in_client.get("/api/v1/parts").json()

    assert status["state"] == "syncing"
    assert status["parts"] == 0
    assert parts["parts"] == []
    assert signed_in_client.get("/healthz").status_code == 200


def test_symbol_svg_is_served(ready):
    """Rendered from the same payload KiCad receives."""
    response = ready.get("/api/v1/parts/1102-0001/symbol.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.startswith("<svg")
    assert "var(--symbol-line)" in response.text


def test_symbol_svg_for_an_unknown_part_is_404(ready):
    assert ready.get("/api/v1/parts/9999-9999/symbol.svg").status_code == 404


def test_the_ipn_page_inlines_the_previews(ready):
    """Inlined rather than linked: an <img> SVG is a separate document, so its
    pins could not be linked to the footprint's pads."""
    html = ready.get("/ipn/1102-0001").text

    assert "<svg" in html
    assert 'class="ksym"' in html and 'class="kfp"' in html
    assert "<img" not in html


def test_footprint_svg_is_served(ready):
    response = ready.get("/api/v1/parts/1102-0001/footprint.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "var(--fp-copper)" in response.text


def test_footprint_svg_is_404_when_no_source_provides_it(ready, library):
    """The part names a footprint; no source has it."""
    part = library.snapshot.parts.parts["1102-0001"]
    part.footprint = "Passives:DoesNotExist"

    assert ready.get("/api/v1/parts/1102-0001/footprint.svg").status_code == 404


def test_pins_and_pads_are_tagged_for_linking(ready):
    html = ready.get("/ipn/1102-0001").text

    assert 'data-pin="1"' in html
    assert 'data-pad="1"' in html
    assert 'data-pin="2"' in html
    assert 'data-pad="2"' in html


def test_the_standalone_svg_endpoints_still_work(ready):
    """The panel's thumbnails use them, where no interaction is needed."""
    assert ready.get("/api/v1/parts/1102-0001/symbol.svg").status_code == 200
    assert ready.get("/api/v1/parts/1102-0001/footprint.svg").status_code == 200


def test_model_endpoint_is_404_without_a_model(ready):
    """The fixture's footprint references no 3D model."""
    assert ready.get("/api/v1/parts/1102-0001/model.step").status_code == 404


def test_the_page_offers_no_viewer_without_a_model(ready):
    html = ready.get("/ipn/1102-0001").text

    assert "model-viewer" not in html
    assert "model-viewer.js" not in html, "8MB of kernel is not loaded speculatively"


def test_search_reports_matches_separately_from_the_catalog(ready):
    """ "12 of 4000" said nothing about the search that produced the twelve."""
    everything = ready.get("/api/v1/parts").json()
    assert everything["matched"] == everything["total"]

    nothing = ready.get("/api/v1/parts?q=zzzz").json()
    assert nothing["matched"] == 0
    assert nothing["total"] == 1, "total still reports the catalog size"


def test_limit_shows_up_as_returned_below_matched(ready, library):
    for n in range(2, 6):
        clone = library.snapshot.parts.parts["1102-0001"]
        library.snapshot.parts.parts[f"1102-000{n}"] = clone

    body = ready.get("/api/v1/parts?limit=2").json()

    assert body["returned"] == 2
    assert body["matched"] == 5
    assert len(body["parts"]) == 2


def test_status_filter(ready, library):
    library.snapshot.parts.parts["1102-0001"].status = "draft"

    assert ready.get("/api/v1/parts?status=draft").json()["matched"] == 1
    assert ready.get("/api/v1/parts?status=approved").json()["matched"] == 0


def test_an_unknown_status_is_rejected_rather_than_matching_nothing(ready):
    assert ready.get("/api/v1/parts?status=banana").status_code == 422


def test_category_filter(ready):
    assert ready.get("/api/v1/parts?category=1102").json()["matched"] == 1
    assert ready.get("/api/v1/parts?category=9999").json()["matched"] == 0


def test_status_offers_the_filters_the_catalog_supports(ready):
    facets = ready.get("/api/v1/status").json()["facets"]

    assert facets["statuses"] == [{"value": "approved", "label": "approved", "count": 1}]
    # The label comes from categories.yaml; the code alone means nothing.
    assert facets["categories"] == [{"value": "1102", "label": "Resistors, fixed", "count": 1}]


def test_facets_omit_what_has_no_parts(ready):
    """A filter that can only ever return nothing is worse than no filter."""
    facets = ready.get("/api/v1/status").json()["facets"]
    assert all(entry["count"] for entry in facets["statuses"] + facets["categories"])


def test_a_part_lists_others_sharing_its_manufacturer_parts(ready, library):
    """The question asked when a manufacturer discontinues something."""
    from dataclasses import replace

    original = library.snapshot.parts.parts["1102-0001"]
    library.snapshot.parts.parts["1102-0002"] = replace(original, ipn="1102-0002")

    body = ready.get("/api/v1/parts/1102-0001").json()
    assert body["related"] == ["1102-0002"]
    assert body["category"] == "1102"

    assert "1102-0002" in ready.get("/ipn/1102-0001").text


def test_a_part_sharing_nothing_lists_nothing(ready):
    assert ready.get("/api/v1/parts/1102-0001").json()["related"] == []


def test_the_issues_page_renders(ready):
    assert ready.get("/issues").status_code == 200


def test_issues_carries_the_severity_totals(ready, library):
    from app.kicad.issues import Issue, Severity

    library.snapshot.parts.issues.append(Issue(Severity.ERROR, "made-up", "boom", "a.yaml"))
    library.snapshot.parts.issues.append(Issue(Severity.WARNING, "made-up", "hmm", "b.yaml"))

    body = ready.get("/api/v1/issues").json()

    assert body["errors"] == 1
    assert body["warnings"] == 1
    # Severity totals describe the library, not the filtered page, so a
    # reader filtered to errors can still see there are warnings to look at.
    filtered = ready.get("/api/v1/issues?severity=error").json()
    assert filtered["total"] == 1
    assert filtered["warnings"] == 1

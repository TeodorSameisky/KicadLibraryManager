"""Catalog endpoints and the IPN page."""

import pytest

from app.kicad.catalog import Catalog
from app.kicad.library import index_repository
from app.kicad.parts import check_against_catalog, load_parts
from app.library_service import LibraryService, Snapshot, SourceStatus, State

from tests.test_parts import CATEGORIES, MPN_YAGEO, PART
from tests.test_library_index import footprint, symbol


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
    write("symbols/Passives.kicad_symdir/R_0603.kicad_sym",
          symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"))
    write("footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
          footprint("R_0603_1608Metric"))

    catalog = Catalog()
    catalog.add("company", index_repository(tmp_path))
    catalog.check_references()
    parts = load_parts(tmp_path)
    check_against_catalog(parts, catalog)

    snapshot = Snapshot(
        catalog=catalog,
        parts=parts,
        sources=[SourceStatus(id="company", name="Company", commit="abc123",
                              symbols=2, parts=1)],
        finished_at=1.0,
        duration=0.2,
    )
    service = LibraryService(sources=[], workdir=tmp_path)
    service._snapshot = snapshot
    service._state = State.READY
    return service


@pytest.fixture()
def ready(client, library):
    client.app.state.library = library
    return client


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


def test_endpoints_are_usable_before_the_first_index(client):
    """The app must answer while the initial clone is still running."""
    service = LibraryService(sources=[], workdir=None)
    service._state = State.SYNCING
    client.app.state.library = service

    status = client.get("/api/v1/status").json()
    parts = client.get("/api/v1/parts").json()

    assert status["state"] == "syncing"
    assert status["parts"] == 0
    assert parts["parts"] == []
    assert client.get("/healthz").status_code == 200


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

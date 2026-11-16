"""The catalog API the panel calls, and the pages a person reads."""

import base64
import threading

import pytest
import zstandard

from app.library.service import LibraryService
from app.library.sources import SourceError
from tests.conftest import OPEN, sign_in
from tests.support import MODEL_PATH, MPN, PART, symbol_file


@pytest.fixture()
def api(library_client):
    return library_client


@pytest.fixture()
def serve(make_client, tree):
    """Serve the standard library after a test has added to it."""

    def build(**environment):
        return sign_in(make_client(tree, **environment))

    tree.standard(model=True)
    return build


# -- /status ----------------------------------------------------------------


def test_status_reports_the_index(api):
    status = api.get("/api/v1/status").json()

    assert status["state"] == "ready" and status["error"] is None
    assert (status["parts"], status["symbols"], status["footprints"], status["models"]) == (
        1, 2, 1, 1,
    )  # fmt: skip
    assert (status["errors"], status["warnings"]) == (0, 0)
    assert status["indexed_at"] > 0
    assert status["sources"] == [
        {
            "id": "company",
            "name": "Company Library",
            "commit": "abc1234def",
            "symbols": 2,
            "footprints": 1,
            "models": 1,
            "parts": 1,
            "error": None,
        }
    ]


def test_status_offers_the_filters_this_library_supports(serve, tree):
    tree.part("1102-0002", PART.replace("approved", "draft"))

    facets = serve().get("/api/v1/status").json()["facets"]

    assert facets["statuses"] == [
        {"value": "draft", "label": "draft", "count": 1},
        {"value": "approved", "label": "approved", "count": 1},
    ]
    # No "Capacitors": a filter that can only return nothing is worse than none.
    assert facets["categories"] == [{"value": "1102", "label": "Resistors, fixed", "count": 2}]


# -- /parts -----------------------------------------------------------------


def test_parts_are_listed_with_what_a_row_shows(api):
    body = api.get("/api/v1/parts").json()

    assert (body["state"], body["total"], body["matched"], body["returned"]) == ("ready", 1, 1, 1)
    assert body["parts"] == [
        {
            "ipn": "1102-0001",
            "description": "Resistor 10k 1% 0.1W 0603",
            "status": "approved",
            "preferred_mpn": "RC0603FR-0710KL",
            "manufacturer": "Yageo",
            "mpn_count": 1,
        }
    ]


@pytest.mark.parametrize("query", ["10k", "YAGEO", "1102", "resistor", "rc0603"])
def test_search_finds_a_part_by_what_is_known_about_it(api, query):
    body = api.get("/api/v1/parts", params={"q": query}).json()
    assert [part["ipn"] for part in body["parts"]] == ["1102-0001"]


def test_a_search_reports_its_matches_apart_from_the_catalog(api):
    """ "12 of 4000" says nothing about the search that produced the twelve."""
    body = api.get("/api/v1/parts?q=zzzz").json()

    assert body["parts"] == []
    assert (body["matched"], body["total"]) == (0, 1)


def test_a_page_is_shorter_than_the_match(serve, tree):
    for number in range(2, 6):
        tree.part(f"1102-000{number}")

    body = serve().get("/api/v1/parts?limit=2").json()

    assert (body["returned"], body["matched"], body["total"]) == (2, 5, 5)
    assert [part["ipn"] for part in body["parts"]] == ["1102-0001", "1102-0002"]


def test_parts_are_narrowed_by_status_and_category(serve, tree):
    tree.part("1102-0002", PART.replace("approved", "draft"))
    client = serve()

    def matched(**params):
        return [p["ipn"] for p in client.get("/api/v1/parts", params=params).json()["parts"]]

    assert matched(status="draft") == ["1102-0002"]
    assert matched(status="obsolete") == []
    assert matched(category="1102") == ["1102-0001", "1102-0002"]
    assert matched(category="9999") == []


@pytest.mark.parametrize("query", ["status=banana", "limit=0", "limit=501", "q=" + "x" * 201])
def test_a_filter_that_makes_no_sense_is_rejected(api, query):
    """Rather than quietly matching nothing."""
    assert api.get(f"/api/v1/parts?{query}").status_code == 422


# -- /parts/{ipn} -----------------------------------------------------------


def test_a_part_is_returned_with_its_sources(api):
    assert api.get("/api/v1/parts/1102-0001").json() == {
        "ipn": "1102-0001",
        "description": "Resistor 10k 1% 0.1W 0603",
        "status": "approved",
        "category": "1102",
        "symbol": "Passives:R_0603",
        "footprint": "Passives:R_0603_1608Metric",
        "fields": {"Value": "10k", "Tolerance": "1%"},
        "mpns": [
            {
                "mpn": "RC0603FR-0710KL",
                "preferred": True,
                "manufacturer": "Yageo",
                "datasheet": "https://example.com/yageo.pdf",
                "lifecycle": "active",
                "known": True,
            }
        ],
        "related": [],
    }


def test_a_part_lists_the_others_that_share_its_sources(serve, tree):
    tree.part("1102-0002")
    tree.part("1102-0003", PART.replace("RC0603FR-0710KL", "UNLISTED"))
    client = serve()

    assert client.get("/api/v1/parts/1102-0001").json()["related"] == ["1102-0002"]
    unlisted = client.get("/api/v1/parts/1102-0003").json()["mpns"][0]
    assert unlisted == {
        "mpn": "UNLISTED",
        "preferred": True,
        "manufacturer": None,
        "datasheet": None,
        "lifecycle": "unknown",
        "known": False,
    }


@pytest.mark.parametrize("suffix", ["", "/assets", "/symbol.svg", "/footprint.svg", "/model.step"])
def test_a_part_that_does_not_exist_is_404(api, suffix):
    assert api.get(f"/api/v1/parts/9999-9999{suffix}").status_code == 404


# -- /parts/{ipn}/assets ----------------------------------------------------


def decode(data: str) -> bytes:
    return zstandard.ZstdDecompressor().decompressobj().decompress(base64.b64decode(data))


def test_the_assets_are_what_the_panel_sends_to_kicad(api):
    body = api.get("/api/v1/parts/1102-0001/assets").json()

    assert body["ipn"] == "1102-0001" and body["warnings"] == []
    assert [(a["command"], a["parameters"]["mode"]) for a in body["assets"]] == [
        ("DL_FOOTPRINT", "SAVE"),
        ("DL_3DMODEL", "SAVE"),
        ("DL_SYMBOL", "PLACE"),
    ]
    symbol = body["assets"][-1]
    assert set(symbol) == {"command", "label", "filename", "parameters", "size_bytes", "data"}
    text = decode(symbol["data"]).decode("utf-8")
    assert '(property "IPN" "1102-0001"' in text
    # The Datasheet field is this deployment's own page for the part.
    assert '(property "Datasheet" "https://library.example.com/ipn/1102-0001"' in text


def test_the_assets_follow_the_deployment_s_settings(serve):
    client = serve(PUBLIC_URL="https://sameisky.tech/kicadLibrary", REMOTE_LIBRARY_PREFIX="corp")

    text = decode(
        client.get("/api/v1/parts/1102-0001/assets").json()["assets"][-1]["data"]
    ).decode()

    assert '"https://sameisky.tech/kicadLibrary/ipn/1102-0001"' in text
    assert '"corp_passives:R_0603_1608Metric"' in text


def test_a_part_that_cannot_be_placed_is_refused_with_the_reason(serve, tree):
    """409, and a sentence the person placing it can act on."""
    tree.write(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym", symbol_file("R_0603", extends="Missing")
    )

    response = serve().get("/api/v1/parts/1102-0001/assets")

    assert response.status_code == 409
    assert "'R_0603' extends 'Missing'" in response.json()["detail"]
    assert "would have no body" in response.json()["detail"]


def test_what_is_missing_comes_back_as_warnings(serve, tree):
    (tree.root / MODEL_PATH).unlink()

    body = serve().get("/api/v1/parts/1102-0001/assets").json()

    assert [a["command"] for a in body["assets"]] == ["DL_FOOTPRINT", "DL_SYMBOL"]
    assert len(body["warnings"]) == 1


# -- drawings and the model -------------------------------------------------


@pytest.mark.parametrize("name,css_class", [("symbol", "ksym"), ("footprint", "kfp")])
def test_drawings_are_served_for_the_panel_s_thumbnails(api, name, css_class):
    response = api.get(f"/api/v1/parts/1102-0001/{name}.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "private, max-age=300", "the library is not public"
    assert response.text.startswith("<svg") and f'class="{css_class}"' in response.text


def test_the_model_is_served_as_the_library_holds_it(api):
    response = api.get("/api/v1/parts/1102-0001/model.step")

    assert response.status_code == 200
    assert response.headers["content-type"] == "model/step"
    assert response.text.startswith("ISO-10303-21;")
    assert "R_0603_1608Metric.step" in response.headers["content-disposition"]


def test_a_part_with_nothing_to_draw_is_404_for_each(serve, tree):
    tree.part("1102-0002", "ipn: 1102-0002\ndescription: Odd\nsymbol: Passives:Ghost\n")
    client = serve()

    for name in ("symbol.svg", "footprint.svg", "model.step"):
        assert client.get(f"/api/v1/parts/1102-0002/{name}").status_code == 404, name


# -- /issues ----------------------------------------------------------------


def test_issues_are_listed_with_where_to_look(serve, tree):
    tree.part("1102-0002", PART.replace("Passives:R_0603_1608Metric", "Passives:Gone"))
    tree.part("1102-0003", "ipn: 1102-0003\nsymbol: Passives:R\n")

    body = serve().get("/api/v1/issues").json()

    assert (body["total"], body["returned"], body["errors"], body["warnings"]) == (2, 2, 1, 1)
    assert body["issues"][0] == {
        "severity": "error",
        "kind": "missing-footprint",
        "message": "1102-0002 names footprint 'Passives:Gone', which no source provides",
        "path": "parts/1102/1102-0002.yaml",
        "source": "company",
    }


def test_issues_are_narrowed_by_severity_but_counted_for_the_library(serve, tree):
    """Someone reading the errors can still see there are warnings."""
    tree.part("1102-0002", PART.replace("Passives:R_0603_1608Metric", "Passives:Gone"))
    tree.part("1102-0003", "ipn: 1102-0003\nsymbol: Passives:R\n")
    client = serve()

    only_errors = client.get("/api/v1/issues?severity=error").json()

    assert [issue["severity"] for issue in only_errors["issues"]] == ["error"]
    assert (only_errors["total"], only_errors["errors"], only_errors["warnings"]) == (1, 1, 1)
    assert client.get("/api/v1/issues?limit=1").json()["returned"] == 1
    assert client.get("/api/v1/issues?severity=fatal").status_code == 422


def test_no_path_in_any_issue_describes_the_server(serve, tree, tmp_path):
    tree.write("symbols/Passives.kicad_symdir/broken.kicad_sym", "(kicad_symbol_lib")
    tree.write("parts/1102/broken.yaml", "ipn: [unclosed\n")

    text = serve().get("/api/v1/issues").text

    assert "broken.kicad_sym" in text and "broken.yaml" in text
    assert tmp_path.name not in text


# -- before and without an index --------------------------------------------


def test_the_app_answers_while_the_first_index_is_running(make_client, tree, tmp_path, provider):
    """Cloning takes long enough that it cannot be waited for."""
    from fastapi.testclient import TestClient

    from app.application import create_app
    from tests.conftest import settings_for, wait_until_indexed

    release = threading.Event()
    tree.standard()

    def slow_sync(source, workdir):
        release.wait(timeout=10)
        return tree.checkout

    library = LibraryService([tree.source], tmp_path / "work", sync=slow_sync)
    app = create_app(settings_for(**OPEN), library=library, identity=provider.client())

    with TestClient(app) as client:
        try:
            assert client.get("/healthz").status_code == 200, "alive, whatever the library does"
            assert client.get("/readyz").status_code == 503
            assert client.get("/readyz").json()["status"] == "syncing"
            assert client.get("/api/v1/status").json()["state"] == "syncing"
            assert client.get("/api/v1/parts").json() == {
                "state": "syncing", "total": 0, "matched": 0, "returned": 0, "parts": [],
            }  # fmt: skip
            assert client.get("/").status_code == 200
        finally:
            release.set()

        wait_until_indexed(client)
        assert client.get("/api/v1/parts").json()["total"] == 1


def test_with_no_library_everything_still_answers(make_client):
    client = make_client(**OPEN)

    assert client.get("/readyz").status_code == 503
    assert client.get("/api/v1/status").json()["state"] == "empty"
    assert client.get("/api/v1/parts").json()["state"] == "empty"
    assert "No sources are configured" in client.get("/").text


def test_a_source_that_cannot_be_cloned_is_shown_where_it_will_be_seen(
    make_client, tree, tmp_path, provider
):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from tests.conftest import settings_for

    def failing_sync(source, workdir):
        raise SourceError("git clone failed: repository not found")

    library = LibraryService([tree.source], tmp_path / "work", sync=failing_sync)
    app = create_app(settings_for(**OPEN), library=library, identity=provider.client())

    with TestClient(app) as client:
        while client.get("/api/v1/status").json()["state"] == "syncing":
            pass

        status = client.get("/api/v1/status").json()
        assert status["state"] == "error"
        assert status["sources"][0]["error"] == "git clone failed: repository not found"
        assert client.get("/readyz").status_code == 503
        assert "git clone failed: repository not found" in client.get("/").text


# -- pages ------------------------------------------------------------------


def test_the_panel_shows_who_is_signed_in(client):
    signed_out = client.get("/panel").text
    assert "Not signed in" in signed_out and ">Sign in<" in signed_out
    assert 'data-signed-in="false"' in signed_out

    signed_in = sign_in(client, name="Ada Lovelace").get("/panel").text
    assert "Ada Lovelace" in signed_in and ">Sign out<" in signed_in
    assert 'data-signed-in="true"' in signed_in and 'data-web-login="true"' in signed_in


def test_the_panel_says_when_anyone_can_read_the_library(make_client):
    text = make_client(**OPEN).get("/panel").text

    assert "Authentication disabled" in text
    assert 'id="session-action"' not in text


def test_the_bridge_is_loaded_before_anything_else_can_run(client):
    """KiCad sends NEW_SESSION as the page loads. A bridge installed later
    never sees it, and nothing can be placed."""
    text = client.get("/panel").text
    head = text.split("</head>")[0]

    assert '<script src="/static/js/kicad-bridge.js"' in head
    assert "defer" not in head and "async" not in head and 'type="module"' not in head
    assert text.index("kicad-bridge.js") < text.index("panel.js")


def test_a_name_is_escaped_on_its_way_into_the_page(client):
    text = sign_in(client, name='<script>alert("x")</script>').get("/panel").text
    assert "<script>alert" not in text


@pytest.mark.parametrize(
    "public_url,prefix",
    [("https://sameisky.tech/kicadLibrary", "/kicadLibrary"), ("https://kicad.sameisky.tech", "")],
    ids=["prefixed", "root"],
)
def test_every_asset_and_link_carries_the_path_prefix(serve, public_url, prefix):
    """A bare /static/... resolves against the site root, not against the app
    -- which is how the panel once loaded with no styles and no scripts."""
    client = serve(PUBLIC_URL=public_url)

    for path in ("/panel", "/", "/ipn/1102-0001", "/issues"):
        text = client.get(path).text
        assert f'data-base-path="{prefix}"' in text, path
        assert f'href="{prefix}/static/css/app.css"' in text, path
        assert f'src="{prefix}/static/js/external-links.js"' in text, path
        assert "//static/" not in text, path
        if prefix:
            assert 'href="/static/' not in text and 'src="/static/' not in text, path
            assert 'href="/panel' not in text and 'href="/ipn' not in text, path


def test_static_files_are_served(client):
    for path in ("css/app.css", "js/app.js", "js/panel.js", "js/kicad-bridge.js", "js/issues.js",
                 "js/pin-map.js", "js/model-viewer.js", "vendor/three.module.min.js",
                 "vendor/three.core.min.js", "vendor/occt-import-js.js", "vendor/occt-import-js.wasm"):  # fmt: skip
        assert client.get(f"/static/{path}").status_code == 200, path


def test_a_part_s_page_shows_the_part(api):
    """This address is what the Datasheet field of every placed symbol holds."""
    text = api.get("/ipn/1102-0001").text

    for expected in (
        "1102-0001",
        "Resistor 10k 1% 0.1W 0603",
        "Resistors, fixed",
        'href="/panel?category=1102"',
        "Passives:R_0603",
        "Tolerance",
        "RC0603FR-0710KL",
        "Yageo",
        "preferred",
        'href="https://example.com/yageo.pdf"',
    ):
        assert expected in text, expected


def test_the_page_inlines_its_drawings_so_pins_can_find_pads(api):
    """Through <img>, each drawing would be a document of its own and nothing
    on the page could reach into it."""
    text = api.get("/ipn/1102-0001").text

    assert 'class="ksym"' in text and 'class="kfp"' in text
    assert "<img" not in text
    for tag in ('data-pin="1"', 'data-pin="2"', 'data-pad="1"', 'data-pad="2"'):
        assert tag in text
    assert "pin-map.js" in text


def test_the_viewer_is_only_offered_where_there_is_a_model(serve, tree):
    """Its kernel is megabytes. It is never loaded just in case."""
    tree.part("1102-0002", "ipn: 1102-0002\ndescription: Bare\nsymbol: Passives:R\n")
    client = serve()

    with_model = client.get("/ipn/1102-0001").text
    without = client.get("/ipn/1102-0002").text

    assert 'id="model-viewer" data-ipn="1102-0001"' in with_model
    assert "model-viewer.js" in with_model
    assert "occt-import-js" not in with_model, "fetched when asked for, not with the page"
    assert "model-viewer" not in without and "pin-map.js" not in without


def test_the_page_lists_the_parts_a_discontinuation_would_reach(serve, tree):
    tree.part("1102-0002", PART.replace("10k", "4k7"))

    text = serve().get("/ipn/1102-0001").text

    assert "Shares a manufacturer part with" in text
    assert 'href="/ipn/1102-0002"' in text and "Resistor 4k7" in text


def test_only_a_web_address_becomes_a_datasheet_link(serve, tree):
    """The value is whatever a file in a repository says it is."""
    tree.write(
        "mpns/RC0603FR-0710KL.yaml",
        MPN.replace("https://example.com/yageo.pdf", "javascript:alert(1)"),
    )

    text = serve().get("/ipn/1102-0001").text

    assert 'href="javascript:' not in text
    assert "javascript:alert(1)" in text, "shown, as text"


def test_text_from_the_library_is_escaped_in_the_page(serve, tree):
    tree.part(
        "1102-0002", PART.replace("Resistor 10k 1% 0.1W 0603", '"<b>bold</b> & <script>x</script>"')
    )

    text = serve().get("/ipn/1102-0002").text

    assert "<b>bold</b>" not in text and "<script>x</script>" not in text
    assert "&lt;b&gt;bold&lt;/b&gt; &amp;" in text


def test_a_part_that_does_not_exist_is_a_page_for_a_browser(api):
    response = api.get("/ipn/9999-9999", headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert "There is no part 9999-9999" in response.text
    assert api.get("/ipn/9999-9999").json() == {"detail": "There is no part 9999-9999"}


def test_the_issues_page_is_a_shell_the_script_fills(api):
    text = api.get("/issues").text
    assert "Library issues" in text and "issues.js" in text


def test_the_status_page_says_whether_the_deployment_works(api):
    text = api.get("/").text

    assert ">ready<" in text
    assert "Company Library" in text and "abc1234d" in text
    assert "https://library.example.com/.well-known/kicad-remote-provider" in text
    # The one value that has to be copied into the identity provider by hand.
    assert "https://library.example.com/auth/callback" in text
    assert "Configuration problems" not in text


def test_the_status_page_shows_what_is_misconfigured(make_client):
    """Nobody reads container logs to find out why a login does nothing."""
    text = make_client(PUBLIC_URL="http://localhost:8000", **OPEN).get("/").text

    assert "Configuration problems" in text
    assert "PUBLIC_URL points at localhost" in text
    assert "anyone who can reach this address can read the library" in text
    assert "/auth/callback" not in text, "no redirect URI to register when sign-in is off"

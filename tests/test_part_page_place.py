"""Placing a part from its own page, which only KiCad's panel offers."""

import re

import pytest

from tests.test_editing import repository, serve, sign_in_librarian  # noqa: F401


def tags(html, name):
    return re.findall(rf"<{name}\b[^>]*>", html)


def test_the_page_has_a_place_button_that_starts_out_hidden(library_client):
    """It is revealed by the script, and only inside KiCad: elsewhere there is
    nothing to place into."""
    html = library_client.get("/ipn/1102-0001").text

    (button,) = [t for t in tags(html, "button") if 'id="place-button"' in t]
    assert 'data-ipn="1102-0001"' in button and " hidden" in button
    # The same plain button as the catalog's Place. The filled one swaps the
    # page's colours, so it is bright in the dark theme and dark in the light.
    assert 'class="button"' in button
    assert 'id="place-result"' in html


def test_a_reader_with_nothing_to_change_has_an_empty_row_that_stays_hidden(library_client):
    """The row holds Place and the edit actions. Without edit rights it would be
    an empty gap until the script reveals it."""
    html = library_client.get("/ipn/1102-0001").text

    (row,) = [t for t in tags(html, "div") if 'id="part-actions"' in t]
    assert " hidden" in row
    assert "status-result" not in html and "part-form.js" not in html


def test_someone_who_may_change_the_part_sees_the_row_and_the_actions(serve):  # noqa: F811
    client = sign_in_librarian(serve())
    html = client.get("/ipn/1102-0001").text

    (row,) = [t for t in tags(html, "div") if 'id="part-actions"' in t]
    assert " hidden" not in row
    assert 'href="/ipn/1102-0001/edit"' in html and 'id="status-result"' in html
    assert "part-form.js" in html


def test_the_page_listens_to_kicad_before_kicad_speaks(library_client):
    """In the head and not deferred, as on the catalog: KiCad opens its session
    as the page loads."""
    html = library_client.get("/ipn/1102-0001").text
    head = html.split("</head>", 1)[0]

    (bridge,) = [t for t in tags(head, "script") if "kicad-bridge.js" in t]
    assert "defer" not in bridge and "async" not in bridge
    assert 'data-server-version="' in bridge


def test_the_placing_code_is_loaded_before_what_uses_it(library_client):
    part = library_client.get("/ipn/1102-0001").text
    assert part.index("static/js/place.js") < part.index("static/js/part-place.js")

    panel = library_client.get("/panel").text
    assert panel.index("static/js/place.js") < panel.index("static/js/panel.js")


@pytest.mark.parametrize("script", ["place.js", "part-place.js"])
def test_the_scripts_are_served(library_client, script):
    assert library_client.get(f"/static/js/{script}").status_code == 200

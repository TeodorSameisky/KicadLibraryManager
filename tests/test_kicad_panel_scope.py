"""What the panel shows: the catalog and the parts, and nothing that is a browser's to do."""

import re

import pytest

from tests.conftest import sign_in_librarian
from tests.test_editing import repository, serve  # noqa: F401

PAGES = ["/panel", "/ipn/1102-0001", "/boms", "/products", "/issues"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_says_whether_it_is_in_kicad_before_it_is_drawn(library_client, path):
    """In the head and not deferred: a script at the end of the page would let
    what the panel hides be seen first."""
    html = library_client.get(path).text
    head = html.split("</head>", 1)[0]

    (script,) = re.findall(r"<script[^>]*in-kicad\.js[^>]*>", head)
    assert "defer" not in script and "async" not in script
    assert library_client.get("/static/js/in-kicad.js").status_code == 200


def test_the_rule_that_hides_them_is_in_the_stylesheet(library_client):
    css = library_client.get("/static/css/app.css").text

    assert ".in-kicad .web-only { display: none !important; }" in css


@pytest.mark.parametrize("path", ["/panel", "/boms", "/products", "/issues"])
def test_the_section_links_are_web_only(library_client, path):
    html = library_client.get(path).text

    assert '<nav class="nav web-only"' in html


def test_the_panel_s_link_to_the_issues_is_web_only(library_client):
    assert 'class="masthead__status web-only"' in library_client.get("/panel").text


def test_a_part_page_keeps_place_and_hides_what_leads_to_a_browser_s_work(serve):  # noqa: F811
    html = sign_in_librarian(serve()).get("/ipn/1102-0001").text

    def tag(needle):
        (found,) = [t for t in re.findall(r"<(?:a|button|div|span)\b[^>]*>", html) if needle in t]
        return found

    assert "web-only" not in tag('id="place-button"')
    assert "web-only" not in tag('id="part-actions"')
    assert "web-only" in tag("/history")
    assert "web-only" in tag("/edit")
    assert "web-only" in tag('data-set-status="obsolete"')
    assert "web-only" in tag('id="status-result"')


def test_the_parts_themselves_are_not_hidden(library_client):
    """What the panel is for: the symbol, the footprint, the fields, the sources."""
    html = library_client.get("/ipn/1102-0001").text
    visible = re.sub(r'<div class="web-only">.*?</div>\s*{?', "", html, flags=re.S)

    for shown in ("Properties", "Approved sources", "Tolerance"):
        assert shown in visible


# -- where used stays, without links the panel cannot follow ----------------------


def used_on_a_bom(client):
    from tests.test_boms import KICAD_DEFAULT

    client.post(
        "/api/v1/boms",
        json={"project": "Amplifier", "revision": "A", "content": KICAD_DEFAULT},
    )
    return client.get("/ipn/1102-0001").text


def test_where_used_is_shown_in_the_panel(library_client):
    html = used_on_a_bom(library_client)
    section = html.split("Where used", 1)[1].split("</table>", 1)[0]

    assert "Where used" in html
    heading = html.split("Where used", 1)[0].rsplit("<h2", 1)[1]
    assert "web-only" not in heading, "the heading is not hidden"
    assert "Amplifier" in section and "Qty" in section


def test_each_project_is_a_link_in_a_browser_and_only_a_name_in_the_panel(library_client):
    html = used_on_a_bom(library_client)
    cell = html.split("Where used", 1)[1].split("<td>", 1)[1].split("</td>", 1)[0]

    assert re.search(r'<a class="web-only" href="/boms/\d+"><strong>Amplifier</strong></a>', cell)
    assert '<strong class="kicad-only">Amplifier</strong>' in cell


def test_the_invitation_to_upload_a_bom_is_web_only(library_client):
    html = library_client.get("/ipn/1102-0001").text
    note = html.split("Not on any stored BOM.", 1)[1].split("</p>", 1)[0]

    assert 'class="web-only"' in note and "/boms" in note


def test_the_panel_only_style_is_the_opposite_of_the_web_only_one(library_client):
    css = library_client.get("/static/css/app.css").text

    assert ".kicad-only { display: none; }" in css
    assert ".in-kicad .kicad-only { display: inline; }" in css

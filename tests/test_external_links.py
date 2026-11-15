"""Links that leave the app, and the icon a datasheet is shown as."""

import shutil

import pytest

from tests.conftest import sign_in
from tests.support import MPN

# The fixtures that build a repository and an app over it.
from tests.test_editing import repository, serve  # noqa: F401

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def sources_table(html):
    return html.split("Approved sources", 1)[1].split("</table>", 1)[0]


# -- the datasheet as an icon -----------------------------------------------


def test_a_datasheet_is_an_icon_with_its_name_kept_for_those_who_need_it(library_client):
    table = sources_table(library_client.get("/ipn/1102-0001").text)

    assert 'class="icon-link"' in table
    assert 'href="https://example.com/yageo.pdf"' in table
    assert 'aria-label="Datasheet for RC0603FR-0710KL (external link)"' in table
    assert 'title="Datasheet"' in table, "what hovering says"
    assert (
        '<svg class="icon"' in table and 'aria-hidden="true"' in table
    ), "the drawing is decoration"


def test_the_word_is_not_repeated_on_every_row(library_client):
    """It is the column heading for a screen reader and nothing a sighted
    reader sees: an icon over an icon would only take the space back."""
    table = sources_table(library_client.get("/ipn/1102-0001").text)

    assert ">Datasheet</a>" not in table, "the link is no longer the word"
    assert '<span class="visually-hidden">Datasheet</span>' in table
    assert table.count("Datasheet") == 3, "the hidden heading, and the link's label and title"


def test_a_datasheet_that_is_not_an_address_is_still_only_text(make_client, tree):
    tree.standard()
    tree.write(
        "mpns/RC0603FR-0710KL.yaml",
        MPN.replace("https://example.com/yageo.pdf", "see the shared drive"),
    )
    table = sources_table(sign_in(make_client(tree)).get("/ipn/1102-0001").text)

    assert "see the shared drive" in table
    assert 'class="icon-link"' not in table, "nothing to follow"


def test_a_part_with_no_datasheet_shows_a_dash(make_client, tree):
    tree.standard()
    tree.write(
        "mpns/RC0603FR-0710KL.yaml", MPN.replace("datasheet: https://example.com/yageo.pdf\n", "")
    )
    table = sources_table(sign_in(make_client(tree)).get("/ipn/1102-0001").text)

    assert 'class="icon-link"' not in table and "—" in table


# -- links that leave the app -----------------------------------------------


def test_a_datasheet_link_is_marked_as_leaving_the_app(library_client):
    """So that, inside KiCad's panel, it offers the address and does not take
    the panel away: KiCad keeps web links in the panel, and there is no way
    back."""
    table = sources_table(library_client.get("/ipn/1102-0001").text)

    assert 'data-external="The datasheet"' in table
    assert 'target="_blank"' in table, "in a browser it still opens in a new tab"
    assert 'rel="noopener noreferrer"' in table


@pytest.mark.parametrize("path", ["/panel", "/ipn/1102-0001", "/", "/boms", "/issues"])
def test_every_page_has_the_script_that_handles_them(library_client, path):
    html = library_client.get(path).text

    assert html.index("static/js/app.js") < html.index("static/js/external-links.js")
    assert library_client.get("/static/js/external-links.js").status_code == 200


def test_the_script_can_tell_whether_it_is_inside_kicad(library_client):
    """The three ways KiCad's WebView lets a page message it, one per platform."""
    app = library_client.get("/static/js/app.js").text

    assert "function inKicad()" in app
    for bridge in ("webkit.messageHandlers.kicad", "chrome.webview", "external.invoke"):
        assert bridge in app, bridge
    assert "inKicad" in app.split("window.App =")[1], "and it is offered to the other scripts"


@needs_git
def test_a_commit_link_is_marked_the_same_way(serve):  # noqa: F811
    """The history page is reachable from inside the panel, and its links to the
    git host would take the panel away just as a datasheet's would."""
    client = sign_in(
        serve(LIBRARY_SOURCES='[{"id":"company","url":"https://github.com/acme/library.git"}]')
    )

    page = client.get("/ipn/1102-0001/history").text

    assert 'data-external="The commit"' in page
    assert 'href="https://github.com/acme/library/commit/' in page
    assert 'target="_blank"' in page

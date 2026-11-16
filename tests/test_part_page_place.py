"""Placing a part from its own page, which only KiCad's panel offers."""

import pathlib
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


def test_the_placing_code_is_one_module_that_both_pages_import(library_client):
    """A page names its own script, and the script says what it uses: the
    order of the tags no longer decides anything."""
    for page, script in (("/ipn/1102-0001", "part-place.js"), ("/panel", "panel.js")):
        assert f'type="module" src="/static/js/{script}"' in library_client.get(page).text
        source = library_client.get(f"/static/js/{script}").text
        assert 'import { place } from "./place.js";' in source, script

    place = library_client.get("/static/js/place.js").text
    assert "export async function place(" in place
    # The bridge is the one thing a module cannot import directly: it is an
    # ordinary script in the head, and bridge.js is how the modules reach it.
    assert 'import { bridge } from "./bridge.js";' in place
    assert library_client.get("/static/js/bridge.js").status_code == 200


@pytest.mark.parametrize("script", ["place.js", "part-place.js"])
def test_the_scripts_are_served(library_client, script):
    assert library_client.get(f"/static/js/{script}").status_code == 200


# -- a module must not give one of its imports' names to something else -----------
#
# The catalog's row once called its button `place`, and handed it to a function
# as a parameter of that name. Inside, `place(ipn)` was then the button and not
# the import: "place is not a function", and only on a click inside KiCad, which
# no test makes. Nothing checks these scripts but this.

_IMPORT = re.compile(r"import\s*\{([^}]*)\}\s*from")
_DECLARED = re.compile(r"\b(?:const|let|var)\s+(\w+)\b")
_DESTRUCTURED = re.compile(r"\b(?:const|let|var)\s*[\[{]([^\]}=]*)[\]}]\s*=")
_FUNCTION = re.compile(r"\bfunction\s*(\w*)\s*\(([^)]*)\)")
_ARROW = re.compile(r"\(([^()]*)\)\s*=>")
_LONE_ARROW = re.compile(r"(?<![\w.)])(\w+)\s*=>")
_CAUGHT = re.compile(r"\bcatch\s*\(\s*(\w+)\s*\)")


def _names(listed):
    for word in listed.split(","):
        word = word.split("=")[0].split(":")[-1].strip().lstrip(".").strip("{}[] ")
        if word.isidentifier():
            yield word


def shadowed(source):
    """The names a script imports and then declares again, as a variable or a
    parameter. Rough -- it reads text, not syntax -- and errs towards saying so."""
    imported = {
        name.split(" as ")[-1].strip()
        for listed in _IMPORT.findall(source)
        for name in listed.split(",")
        if name.strip()
    }
    body = _IMPORT.sub("", source)
    declared = set(_DECLARED.findall(body)) | set(_CAUGHT.findall(body))
    declared |= set(_LONE_ARROW.findall(body))
    for listed in _DESTRUCTURED.findall(body):
        declared |= set(_names(listed))
    for name, parameters in _FUNCTION.findall(body):
        declared |= {name} | set(_names(parameters))
    for parameters in _ARROW.findall(body):
        declared |= set(_names(parameters))
    return sorted(imported & declared)


def test_the_check_sees_a_parameter_that_takes_an_import_s_name():
    broken = (
        'import { place } from "./place.js";\n'
        "async function placePart(ipn, place, note) { await place(ipn); }\n"
    )
    assert shadowed(broken) == ["place"]
    assert shadowed(broken.replace("ipn, place, note", "ipn, placer, note")) == []
    assert shadowed('import { el } from "./app.js";\nconst el = 1;') == ["el"]
    assert shadowed('import { el } from "./app.js";\nitems.map((el) => el);') == ["el"]
    assert shadowed('import { el } from "./app.js";\nitems.map(el => el);') == ["el"]


def test_no_script_gives_an_import_s_name_to_something_else():
    scripts = sorted((pathlib.Path(__file__).parent.parent / "app/static/js").glob("*.js"))
    assert len(scripts) > 10
    assert {script.name: shadowed(script.read_text(encoding="utf-8")) for script in scripts} == {
        script.name: [] for script in scripts
    }

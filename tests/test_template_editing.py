"""Editing the templates that say what each category's parts carry."""

import shutil

import pytest

from app.library import template_editing as tpl
from app.library.editing import EditError
from app.library.parts import load_parts
from tests.conftest import sign_in_librarian
from tests.support import PART

# The fixtures that build a repository and an app over it.
from tests.test_editing import REQUEST, repository, serve, snapshot, web  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

RESISTOR_FORM = tpl.TemplateForm(
    value="{Resistance} {Tolerance}",
    fields=(
        tpl.FieldSpec("Resistance", required=True, example="10k"),
        tpl.FieldSpec("Tolerance", required=True, options=("0.1%", "1%", "5%"), default="1%"),
        tpl.FieldSpec("Package", options=("0402", "0603")),
    ),
)


def problems_of(form, library, category="1102"):
    with pytest.raises(EditError) as refused:
        tpl.check_template(form, library, category)
    return refused.value.problems


# -- checking ---------------------------------------------------------------


def test_a_sensible_template_is_accepted(snapshot):  # noqa: F811
    tpl.check_template(RESISTOR_FORM, snapshot, "1102")
    tpl.check_template(tpl.TemplateForm(), snapshot, "1102")  # one that asks nothing


@pytest.mark.parametrize(
    "form,expected",
    [
        (tpl.TemplateForm(fields=(tpl.FieldSpec(" "),)), "Every field needs a name."),
        (tpl.TemplateForm(fields=(tpl.FieldSpec("A"), tpl.FieldSpec("a"))), "a is listed twice."),
        (tpl.TemplateForm(fields=(tpl.FieldSpec("IPN"),)), "IPN is set from the part itself"),
        (
            tpl.TemplateForm(fields=(tpl.FieldSpec("T", options=("1%", "1%")),)),
            "T lists an option twice.",
        ),
        (
            tpl.TemplateForm(fields=(tpl.FieldSpec("T", options=("1%",), default="5%"),)),
            "T: the default '5%' is not one of its options.",
        ),
        (
            tpl.TemplateForm(value="{Resistance}", fields=(tpl.FieldSpec("R"),)),
            "The value pattern uses {Resistance}, which is not one of the fields.",
        ),
    ],
    ids=["no-name", "twice", "reserved", "option-twice", "default", "pattern"],
)
def test_what_is_wrong_with_a_template_is_said(snapshot, form, expected):  # noqa: F811
    assert any(expected in problem for problem in problems_of(form, snapshot))


def test_a_template_for_a_category_that_does_not_exist_is_refused(snapshot):  # noqa: F811
    found = problems_of(RESISTOR_FORM, snapshot, category="9999")
    assert "There is no category 9999; it has to be in categories.yaml first." in found


# -- writing ----------------------------------------------------------------


def test_a_template_is_written_as_a_person_would_write_it(tree):
    tree.standard()

    message = tpl.write_template(tree.root, "1102", "Resistors, fixed", RESISTOR_FORM)

    assert message == "Add the template for 1102: Resistors, fixed"
    assert (tree.root / "templates/1102.yaml").read_text(encoding="utf-8") == (
        "category: '1102'\n"
        "value: '{Resistance} {Tolerance}'\n"
        "\n"
        "fields:\n"
        "  - name: Resistance\n"
        "    required: true\n"
        "    example: 10k\n"
        "  - name: Tolerance\n"
        "    required: true\n"
        "    options: [0.1%, 1%, 5%]\n"
        "    default: 1%\n"
        "  - name: Package\n"
        "    options: ['0402', '0603']\n"
    )
    index = load_parts(tree.root)
    assert index.templates["1102"].fields[2].options == ("0402", "0603"), "not the number 402"
    again = tpl.write_template(tree.root, "1102", "Resistors, fixed", RESISTOR_FORM)
    assert again.startswith("Change the template")


def test_what_is_written_reads_back_as_the_same_template(tree):
    tricky = tpl.TemplateForm(
        value="{Note: odd}",
        fields=(
            tpl.FieldSpec("Note: odd", example="a, b [c] # d", default="yes"),
            tpl.FieldSpec("Pins", options=("2", "4", "08", "true", "1.5")),
        ),
    )
    tree.standard()
    tpl.write_template(tree.root, "1102", "x", tricky)

    read = load_parts(tree.root).templates["1102"]

    assert (read.fields[0].name, read.fields[0].example, read.fields[0].default) == (
        "Note: odd",
        "a, b [c] # d",
        "yes",
    )
    assert read.fields[1].options == ("2", "4", "08", "true", "1.5"), "all text, as typed"
    assert read.value == "{Note: odd}"


def test_anything_that_looks_like_a_number_is_quoted(tree):
    """`0805` is text to this parser and the number 805 to a YAML 1.2 one --
    and to a person reading the file. Quoted, it is the same to all of them."""
    form = tpl.TemplateForm(
        fields=(
            tpl.FieldSpec("Package", options=("0402", "0805", "1206", "SOT-23")),
            tpl.FieldSpec("Pins", options=("8", "1.5", "1e3", "-5"), default="8"),
        )
    )
    tree.standard()
    tpl.write_template(tree.root, "1102", "x", form)

    text = (tree.root / "templates/1102.yaml").read_text(encoding="utf-8")

    assert "options: ['0402', '0805', '1206', SOT-23]" in text
    assert "options: ['8', '1.5', '1e3', '-5']" in text
    assert "default: '8'" in text
    assert "default: 1%" not in text and "example" not in text, "and nothing else is quoted"
    read = load_parts(tree.root).templates["1102"].fields
    assert read[0].options == ("0402", "0805", "1206", "SOT-23")
    assert read[1].default == "8"


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "1102.yaml"])
def test_a_category_code_cannot_name_another_file(tree, bad):
    tree.standard()

    with pytest.raises(EditError, match="is not a category code"):
        tpl.write_template(tree.root, bad, "x", RESISTOR_FORM)

    assert not (tree.root.parent / "x.yaml").exists()


def test_a_template_is_removed(tree):
    tree.standard()
    tpl.write_template(tree.root, "1102", "x", RESISTOR_FORM)

    message = tpl.delete_template(tree.root, "1102", "Resistors, fixed")

    assert message == "Remove the template for 1102: Resistors, fixed"
    assert load_parts(tree.root).templates == {}
    with pytest.raises(EditError, match="There is no template for 1102"):
        tpl.delete_template(tree.root, "1102", "x")


def test_the_parts_a_template_leaves_short_are_counted(tree):
    """The work a change has made: the standard part carries Value only."""
    tree.standard()
    tpl.write_template(tree.root, "1102", "x", RESISTOR_FORM)
    tree.part("1102-0002", PART.replace("  Value: 10k\n", "  Resistance: 1k\n  Tolerance: 1%\n"))

    short = tpl.shortfall(tree.snapshot(), "1102")

    assert (short.parts, short.of) == (1, 2)
    assert tpl.shortfall(tree.snapshot(), "1101") == tpl.Shortfall(parts=0, of=0)


# -- through the web --------------------------------------------------------

BODY = {
    "value": "{Resistance} {Tolerance}",
    "fields": [
        {"name": "Resistance", "required": True, "example": "10k"},
        {
            "name": "Tolerance",
            "required": True,
            "options": ["0.1%", "1%", "5%", " "],
            "default": "1%",
        },
    ],
}


def test_a_librarian_creates_a_template(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())

    response = librarian.put("/api/v1/templates/1102", json=BODY)

    assert response.status_code == 200
    # The standard part carries Value only, so it now falls short.
    assert response.json() == {"category": "1102", "parts": 1, "falling_short": 1}
    assert repository.last("%an|%s") == "Grace Hopper|Add the template for 1102: Resistors, fixed"
    assert "options: [0.1%, 1%, 5%]" in repository.show("templates/1102.yaml"), "no empty option"
    # In force at once: a new resistor now has to say its resistance.
    refused = librarian.post("/api/v1/parts", json={**REQUEST, "fields": {"Value": "330R"}})
    assert refused.status_code == 422
    assert "Resistance is required." in refused.json()["problems"]


def test_a_librarian_changes_and_removes_a_template(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())
    librarian.put("/api/v1/templates/1102", json=BODY)

    changed = librarian.put("/api/v1/templates/1102", json={"fields": []})
    assert changed.json()["falling_short"] == 0, "a template that asks nothing"
    assert repository.last("%s") == "Change the template for 1102: Resistors, fixed"

    librarian.put("/api/v1/templates/1102", json=BODY)
    removed = librarian.delete("/api/v1/templates/1102")
    assert removed.json() == {"category": "1102", "parts": 1, "falling_short": 0}
    assert repository.last("%s") == "Remove the template for 1102: Resistors, fixed"
    assert librarian.delete("/api/v1/templates/1102").status_code == 422, "nothing left to remove"


def test_a_template_that_makes_no_sense_lists_what_to_fix(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())
    before = repository.commits()

    response = librarian.put(
        "/api/v1/templates/1102",
        json={"value": "{Gone}", "fields": [{"name": "IPN"}, {"name": "A"}, {"name": "a"}]},
    )

    assert response.status_code == 422
    assert len(response.json()["problems"]) == 3
    assert repository.commits() == before


def test_only_a_librarian_edits_templates(web, repository):  # noqa: F811
    """Ada is a designer."""
    before = repository.commits()

    assert web.put("/api/v1/templates/1102", json=BODY).status_code == 403
    assert web.delete("/api/v1/templates/1102").status_code == 403
    for page in ("/templates", "/templates/1102/edit"):
        refused = web.get(page, headers={"Accept": "text/html"})
        assert refused.status_code == 403 and "Only a librarian can do this." in refused.text
    assert repository.commits() == before
    assert 'href="/templates"' not in web.get("/panel").text, "and is not offered the way in"


def test_templates_need_a_session(serve):  # noqa: F811
    client = serve()
    for method, path in (
        ("PUT", "/api/v1/templates/1102"),
        ("DELETE", "/api/v1/templates/1102"),
        ("GET", "/templates"),
        ("GET", "/templates/1102/edit"),
    ):
        assert client.request(method, path, json=BODY).status_code == 401, path


def test_a_template_for_an_undeclared_category_is_404(serve):  # noqa: F811
    librarian = sign_in_librarian(serve())

    assert librarian.put("/api/v1/templates/9999", json=BODY).status_code == 404
    assert librarian.get("/templates/9999/edit").status_code == 404


def test_the_templates_page_lists_each_category(serve):  # noqa: F811
    librarian = sign_in_librarian(serve())
    librarian.put("/api/v1/templates/1102", json=BODY)

    text = librarian.get("/templates").text

    assert "1102" in text and "Resistors, fixed" in text
    assert "2 fields, 2 required" in text and "1 of 1 parts fall short" in text
    assert "none: asks nothing of its parts" in text, "1101 has no template"
    assert 'href="/templates/1102/edit"' in text and ">Edit<" in text
    assert 'href="/templates/1101/edit"' in text and ">Create<" in text
    assert 'aria-current="page">Templates<' in text


def test_the_edit_page_starts_from_the_template_as_it_is(serve):  # noqa: F811
    librarian = sign_in_librarian(serve())
    new = librarian.get("/templates/1102/edit").text
    assert "template-form.js" in new and 'id="template-remove"' not in new

    librarian.put("/api/v1/templates/1102", json=BODY)
    text = librarian.get("/templates/1102/edit").text

    assert 'id="template-remove"' in text
    assert '"name": "Tolerance"' in text and '"options": ["0.1%", "1%", "5%"]' in text
    assert "IPN, Datasheet, Footprint" in text, "the names that cannot be used are said"


def test_a_deployment_that_only_reads_cannot_edit_templates(serve):  # noqa: F811
    librarian = sign_in_librarian(serve(writable=False))

    assert librarian.put("/api/v1/templates/1102", json=BODY).status_code == 503
    assert "cannot change the library" in librarian.get("/templates/1102/edit").text
    assert 'href="/templates"' not in librarian.get("/panel").text

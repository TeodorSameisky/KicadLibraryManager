"""Part templates: what every part in a category has to say about itself."""

import pytest

from app.kicad.symbol import properties
from app.library import editing
from app.library.editing import EditError, PartForm
from app.library.issues import errors, warnings
from app.library.parts import Template, TemplateField, load_parts
from app.library.placement import PlacementOptions, placed_symbol
from tests.conftest import sign_in
from tests.support import PART

RESISTOR = """\
category: "1102"
value: "{Resistance}"
fields:
  - name: Resistance
    required: true
    example: 10k
  - name: Tolerance
    required: true
    options: [0.1%, 1%, 5%]
    default: 1%
  - name: Power
    example: 0.1W
"""

# A resistor that says what the template asks of it.
GOOD = PART.replace("  Value: 10k\n", "  Resistance: 10k\n")


def kinds(issues):
    return [issue.kind for issue in issues]


# -- reading a template -----------------------------------------------------


def test_a_template_is_read_from_the_library(tree):
    tree.standard().write("templates/1102.yaml", RESISTOR)
    tree.write("parts/1102/1102-0001.yaml", GOOD)

    index = load_parts(tree.root)

    assert index.issues == []
    template = index.templates["1102"]
    assert template.value == "{Resistance}"
    assert template.fields == (
        TemplateField("Resistance", required=True, example="10k"),
        TemplateField("Tolerance", required=True, options=("0.1%", "1%", "5%"), default="1%"),
        TemplateField("Power", example="0.1W"),
    )


def test_a_field_may_be_just_its_name(tree):
    """The shorthand, for a field that is offered and nothing more."""
    tree.standard().write("templates/1102.yaml", "fields:\n  - Value\n  - name: Voltage\n")

    template = load_parts(tree.root).templates["1102"]

    assert template.fields == (TemplateField("Value"), TemplateField("Voltage"))
    assert template.category == "1102", "the file is named after its category"


def test_options_are_text_whatever_yaml_made_of_them(tree):
    tree.standard().write(
        "templates/1102.yaml", "fields:\n  - name: Pins\n    options: [2, 4, 8]\n"
    )
    assert load_parts(tree.root).templates["1102"].fields[0].options == ("2", "4", "8")


@pytest.mark.parametrize(
    "relative,text,kind",
    [
        ("templates/9999.yaml", "fields: []\n", "unknown-category"),
        ("templates/1102.yaml", "- not\n- a mapping\n", "bad-template"),
        ("templates/1102.yaml", "fields: Resistance\n", "bad-template"),
        ("templates/1102.yaml", "fields:\n  - required: true\n", "bad-template"),
        ("templates/1102.yaml", "fields:\n  - name: A\n    options: yes\n", "bad-template"),
        ("templates/1102.yaml", "fields:\n  - A\n  - A\n", "bad-template"),
        ("templates/1102.yaml", "fields: [unclosed\n", "invalid-yaml"),
    ],
    ids=["undeclared", "not-a-mapping", "fields-not-a-list", "no-name", "options", "twice", "yaml"],
)
def test_a_template_that_cannot_be_used_is_reported(tree, relative, text, kind):
    tree.standard().write(relative, text)

    index = load_parts(tree.root)

    assert kind in kinds(errors(index.issues))
    assert "1102-0001" in index.parts, "and the parts still load"


def test_a_template_saying_it_is_for_another_category_is_flagged(tree):
    tree.standard().write("templates/1102.yaml", 'category: "1101"\nfields: []\n')

    index = load_parts(tree.root)

    assert "name-mismatch" in kinds(warnings(index.issues))
    assert "1102" in index.templates, "the file name is what counts"


# -- holding parts to it ----------------------------------------------------

TEMPLATE = Template(
    "1102",
    fields=(
        TemplateField("Resistance", required=True),
        TemplateField("Tolerance", required=True, options=("1%", "5%")),
        TemplateField("Power"),
    ),
    value="{Resistance} {Tolerance}",
)


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"Resistance": "10k", "Tolerance": "1%"}, []),
        ({"Resistance": "10k", "Tolerance": "1%", "Power": "0.1W", "Extra": "x"}, []),
        ({"Tolerance": "1%"}, ["Resistance is required."]),
        ({"Resistance": "  ", "Tolerance": "1%"}, ["Resistance is required."]),
        (
            {"Resistance": "10k", "Tolerance": "2%"},
            ["Tolerance must be one of 1%, 5%; it is '2%'."],
        ),
        ({}, ["Resistance is required.", "Tolerance is required."]),
    ],
    ids=["complete", "with-more", "missing", "blank", "not-an-option", "empty"],
)
def test_what_a_part_is_missing_is_said(fields, expected):
    """A field the template does not name is the part's own business."""
    assert TEMPLATE.problems(fields) == expected


def test_existing_parts_that_fall_short_are_listed_as_issues(tree):
    """Which is the worklist when a template is added to a library that
    already has parts, or an old library is brought in."""
    tree.standard().write("templates/1102.yaml", RESISTOR)  # 1102-0001 has Value, not Resistance

    index = load_parts(tree.root)

    (issue,) = index.issues
    assert (issue.kind, issue.path) == ("template", "parts/1102/1102-0001.yaml")
    assert issue.message == "1102-0001: Resistance is required."
    assert errors(index.issues) == [], "worth fixing, but the part still places"


def test_a_category_without_a_template_asks_nothing(tree):
    tree.standard().write("templates/1101.yaml", RESISTOR.replace("1102", "1101"))
    assert load_parts(tree.root).issues == []


# -- on a request -----------------------------------------------------------


def request(**changes) -> PartForm:
    values = {
        "category": "1102",
        "description": "Resistor 330R",
        "symbol": "Passives:R_0603",
        "fields": {"Resistance": "330R", "Tolerance": "1%"},
    }
    return PartForm(**{**values, **changes})


@pytest.fixture()
def snapshot(tree):
    tree.standard().write("templates/1102.yaml", RESISTOR)
    return tree.snapshot()


def test_a_request_that_fills_the_template_is_accepted(snapshot):
    editing.check(request(), snapshot, new=True)


def test_a_request_missing_a_mandatory_field_is_refused(snapshot):
    with pytest.raises(EditError) as refused:
        editing.check(request(fields={"Tolerance": "10%"}), snapshot, new=True)

    assert refused.value.problems == [
        "Resistance is required.",
        "Tolerance must be one of 0.1%, 1%, 5%; it is '10%'.",
    ]


def test_an_edit_is_held_to_the_template_of_the_part_s_own_category(snapshot):
    """The form does not say the category when editing; the part does."""
    incomplete = request(category="", fields={})

    with pytest.raises(EditError, match="Resistance is required"):
        editing.check(incomplete, snapshot, new=False, category="1102")
    editing.check(incomplete, snapshot, new=False, category="1101"), "no template there"


def test_a_field_left_empty_is_not_written(tree):
    """Written as "", it would blank the symbol's own value on the sheet."""
    tree.standard()

    ipn, _ = editing.write_new_part(
        tree.root, request(fields={"Resistance": "330R", "Power": "", "Tolerance": " 1% "})
    )

    assert load_parts(tree.root).parts[ipn].fields == {"Resistance": "330R", "Tolerance": "1%"}


# -- on the placed symbol ---------------------------------------------------


def placed_fields(tree, ipn="1102-0001"):
    snapshot = tree.snapshot()
    options = PlacementOptions(public_url="https://library.example.com")
    return properties(placed_symbol(snapshot.part(ipn), snapshot, options).node)


def test_the_template_builds_the_value_kicad_shows(tree):
    """A resistor that carries its resistance as "Resistance" has no Value,
    and would otherwise show the symbol's name on the sheet."""
    tree.standard().write("templates/1102.yaml", RESISTOR)
    tree.write("parts/1102/1102-0001.yaml", GOOD)

    fields = placed_fields(tree)

    assert fields["Value"] == "10k"
    assert fields["Resistance"] == "10k", "and the field itself is still carried"


def test_a_value_the_part_sets_itself_is_left_alone(tree):
    tree.standard().write("templates/1102.yaml", RESISTOR)
    tree.write("parts/1102/1102-0001.yaml", GOOD.replace("fields:\n", "fields:\n  Value: 10k 1%\n"))

    assert placed_fields(tree)["Value"] == "10k 1%"


@pytest.mark.parametrize(
    "pattern,fields,expected",
    [
        ("{Resistance} {Tolerance}", {"Resistance": "10k", "Tolerance": "1%"}, "10k 1%"),
        ("{Resistance} {Tolerance}", {"Resistance": "10k"}, "10k"),
        ("{ Resistance }", {"Resistance": "10k"}, "10k"),
        ("{Resistance}", {}, ""),
        ("", {"Resistance": "10k"}, ""),
        ("{Resistance.__class__}", {"Resistance": "10k"}, ""),
    ],
    ids=["two", "one-missing", "spaces", "all-missing", "no-pattern", "not-an-attribute"],
)
def test_a_value_pattern_is_filled_from_the_fields(pattern, fields, expected):
    """Names in braces and nothing cleverer: the pattern comes from a file."""
    assert Template("1102", value=pattern).value_for(fields) == expected


def test_without_a_pattern_the_symbol_keeps_its_own_value(tree):
    tree.standard().write("templates/1102.yaml", RESISTOR.replace('value: "{Resistance}"\n', ""))
    tree.write("parts/1102/1102-0001.yaml", GOOD)

    assert placed_fields(tree)["Value"] == "R_0603"


# -- through the web --------------------------------------------------------


def test_the_form_is_told_each_category_s_template(make_client, tree):
    tree.standard().write("templates/1102.yaml", RESISTOR)
    client = sign_in(make_client(tree))

    templates = client.get("/api/v1/library/choices").json()["templates"]

    assert list(templates) == ["1102"]
    assert templates["1102"]["value"] == "{Resistance}"
    assert templates["1102"]["fields"][1] == {
        "name": "Tolerance",
        "required": True,
        "options": ["0.1%", "1%", "5%"],
        "example": "",
        "default": "1%",
    }


def test_template_shortfalls_show_on_the_issues_list(make_client, tree):
    tree.standard().write("templates/1102.yaml", RESISTOR)
    client = sign_in(make_client(tree))

    issues = client.get("/api/v1/issues?severity=warning").json()["issues"]

    assert [(i["kind"], i["message"]) for i in issues] == [
        ("template", "1102-0001: Resistance is required.")
    ]

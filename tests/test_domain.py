"""The domain: what is asked of a part, and who may change one.

Nothing here touches a repository, a database or a request, which is the
point of the package: these run in milliseconds.
"""

import pytest

from app.domain import policy, rules
from app.domain.parts import Lifecycle, Mpn, Part, PartStatus, is_web_address
from app.domain.policy import Actor
from app.domain.refs import LibraryRef
from app.domain.rules import Finding, Rule
from app.domain.templates import Template, TemplateField, placeholders
from app.library import editing
from app.library import parts as parts_loader

# -- the words for a part's state ---------------------------------------------


def test_statuses_and_lifecycles_are_the_text_the_files_hold():
    """A part read from a file has plain text for its status. The names here
    have to compare equal to it, and be written back as it."""
    assert Part("1102-0001", status="approved").status == PartStatus.APPROVED
    assert Mpn("X", lifecycle="nrnd").lifecycle == Lifecycle.NRND
    assert f"{PartStatus.DRAFT}" == "draft"
    assert ", ".join(PartStatus) == "draft, approved, obsolete"
    assert "approved" in PartStatus and "finished" not in PartStatus


def test_a_part_starts_out_a_draft_and_a_manufacturer_part_unknown():
    assert Part("1102-0001").status == "draft"
    assert Mpn("X").lifecycle == "unknown"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://example.com/a.pdf", True),
        ("http://example.com/a.pdf", True),
        ("javascript:alert(1)", False),
        ("example.com/a.pdf", False),
        ("", False),
    ],
)
def test_only_a_web_address_can_be_linked_to(text, expected):
    assert is_web_address(text) is expected
    assert (Mpn("X", datasheet=text).datasheet_url == text) is expected


# -- what is asked of a part --------------------------------------------------


class Assets:
    """A library with one usable symbol, one broken one, and one footprint."""

    def symbol_ancestry(self, ref: LibraryRef) -> object:
        if ref.name == "R_0603":
            return [ref]
        if ref.name == "Broken":
            raise ValueError("Broken extends Missing, which is not in the library")
        raise LookupError(str(ref))

    def footprint(self, ref: LibraryRef) -> object | None:
        return ref if ref.name == "R_0603_1608Metric" else None


RESISTOR = Template(
    "1102",
    fields=(
        TemplateField("Resistance", required=True),
        TemplateField("Tolerance", options=("1%", "5%")),
    ),
)


def findings(**changes) -> list[Finding]:
    asked = {
        "description": "Resistor 10k 1% 0603",
        "symbol": "Passives:R_0603",
        "footprint": "Passives:R_0603_1608Metric",
        "fields": {"Resistance": "10k", "Tolerance": "1%"},
        "preferred": [True, False],
        "template": RESISTOR,
        "assets": Assets(),
    }
    return rules.part_findings(**{**asked, **changes})


def test_a_part_that_says_everything_falls_short_of_nothing():
    assert findings() == []


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"description": "  "}, Finding(Rule.NO_DESCRIPTION)),
        ({"symbol": ""}, Finding(Rule.NO_SYMBOL)),
        ({"symbol": "R_0603"}, Finding(Rule.UNQUALIFIED_SYMBOL)),
        ({"symbol": "Passives:Nope"}, Finding(Rule.MISSING_SYMBOL)),
        (
            {"symbol": "Passives:Broken"},
            Finding(Rule.UNPLACEABLE, "Broken extends Missing, which is not in the library"),
        ),
        ({"footprint": "R_0603"}, Finding(Rule.UNQUALIFIED_FOOTPRINT)),
        ({"footprint": "Passives:Nope"}, Finding(Rule.MISSING_FOOTPRINT)),
        ({"fields": {"Tolerance": "1%"}}, Finding(Rule.TEMPLATE, "Resistance is required.")),
        ({"preferred": [True, True]}, Finding(Rule.MULTIPLE_PREFERRED)),
    ],
    ids=lambda value: value.rule.value if isinstance(value, Finding) else "",
)
def test_each_thing_a_part_can_fall_short_of(changes, expected):
    assert findings(**changes) == [expected]


def test_everything_wrong_is_found_not_just_the_first():
    found = findings(description="", symbol="", fields={}, preferred=[True, True])

    assert [finding.rule for finding in found] == [
        Rule.NO_DESCRIPTION,
        Rule.NO_SYMBOL,
        Rule.TEMPLATE,
        Rule.MULTIPLE_PREFERRED,
    ]


def test_a_part_need_not_name_a_footprint():
    """The symbol may carry its own."""
    assert findings(footprint="") == []


def test_without_assets_the_drawings_are_not_looked_up():
    """For the loader, which reads one repository and cannot say what another
    one provides. `asset_findings` is asked separately, with all of them."""
    assert findings(symbol="Passives:Nope", footprint="Passives:Nope", assets=None) == []
    assert rules.asset_findings("Passives:Nope", "Passives:Nope", Assets()) == [
        Finding(Rule.MISSING_SYMBOL),
        Finding(Rule.MISSING_FOOTPRINT),
    ]


def test_a_category_without_a_template_asks_nothing_of_the_fields():
    assert findings(fields={}, template=None) == []


def test_every_rule_has_its_words_for_the_issue_list_and_for_the_form():
    """A rule added to the domain and forgotten in either place would be a
    KeyError the first time a part broke it."""
    part = Part("1102-0001", symbol="Passives:R", footprint="Passives:F", path="parts/x.yaml")

    for rule in Rule:
        issue = parts_loader.part_issue(Finding(rule, "why"), part)
        assert issue.kind == rule.value and issue.message and issue.path == "parts/x.yaml"
        assert editing._SENTENCES[rule]


# -- templates ----------------------------------------------------------------


def test_the_fields_a_value_pattern_is_built_from():
    assert placeholders("{Resistance} {Tolerance} {Resistance}") == ("Resistance", "Tolerance")
    assert placeholders("{ Resistance }") == ("Resistance",)
    assert placeholders("10k") == ()


# -- who may change what ------------------------------------------------------


@pytest.mark.parametrize(
    "status,librarian,expected",
    [
        ("draft", False, True),
        ("draft", True, True),
        ("approved", False, False),
        ("approved", True, True),
        ("obsolete", False, False),
        ("obsolete", True, True),
        # Not a status at all: a mistake in the file, and not anyone's draft.
        ("finished", False, False),
    ],
)
def test_a_draft_is_anyones_to_correct_and_the_rest_is_a_librarians(status, librarian, expected):
    part = Part("1102-0001", status=status)

    assert policy.may_edit(Actor(librarian=librarian), part) is expected

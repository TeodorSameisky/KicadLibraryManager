"""Loading internal part numbers and the manufacturer parts behind them."""

from app.library.issues import errors, warnings
from app.library.parts import Mpn, Part, load_parts
from tests.support import MPN, PART


def kinds(issues):
    return {issue.kind for issue in issues}


def test_a_complete_library_loads_cleanly(tree):
    index = load_parts(tree.standard().root)

    assert index.issues == []
    part = index.parts["1102-0001"]
    assert part.description == "Resistor 10k 1% 0.1W 0603"
    assert part.status == "approved"
    assert part.category == "1102"
    assert part.symbol == "Passives:R_0603"
    assert part.fields == {"Value": "10k", "Tolerance": "1%"}
    assert part.preferred_mpn == "RC0603FR-0710KL"
    assert part.path == "parts/1102/1102-0001.yaml", "relative to the repository"

    assert index.mpns["RC0603FR-0710KL"].manufacturer == "Yageo"
    assert index.categories["1102"].name == "Resistors, fixed"


def test_field_values_are_text_whatever_yaml_made_of_them(tree):
    """`Value: 100` is a number to YAML and a string to KiCad."""
    tree.standard().part(
        "1102-0002",
        """\
        ipn: 1102-0002
        description: Resistor
        symbol: Passives:R_0603
        fields:
          Value: 100
          Fitted: true
        """,
    )

    assert load_parts(tree.root).parts["1102-0002"].fields == {"Value": "100", "Fitted": "True"}


def test_which_parts_use_a_manufacturer_part(tree):
    """The question asked when a manufacturer discontinues something."""
    tree.standard().part("1102-0002")

    index = load_parts(tree.root)

    assert [p.ipn for p in index.parts_using("RC0603FR-0710KL")] == ["1102-0001", "1102-0002"]
    assert index.parts_using("NOT-USED") == []


# -- sources ----------------------------------------------------------------


def test_sources_may_be_plain_strings(tree):
    """The shorthand, for a part with one source."""
    tree.standard().part(
        "1102-0002",
        """\
        ipn: 1102-0002
        description: Resistor
        symbol: Passives:R_0603
        mpns:
          - RC0603FR-0710KL
        """,
    )

    part = load_parts(tree.root).parts["1102-0002"]

    assert part.preferred_mpn == "RC0603FR-0710KL", "a lone source is the one to use"
    assert not part.mpns[0].preferred


def test_a_source_with_no_file_is_an_error(tree):
    tree.standard().part("1102-0002", PART.replace("RC0603FR-0710KL", "GHOST"))
    assert "unknown-mpn" in kinds(errors(load_parts(tree.root).issues))


def test_two_preferred_sources_is_an_error(tree):
    tree.standard().write("mpns/OTHER.yaml", MPN.replace("RC0603FR-0710KL", "OTHER"))
    tree.part(
        "1102-0002",
        """\
        ipn: 1102-0002
        description: Resistor
        symbol: Passives:R_0603
        mpns:
          - {mpn: RC0603FR-0710KL, preferred: true}
          - {mpn: OTHER, preferred: true}
        """,
    )

    assert "multiple-preferred" in kinds(errors(load_parts(tree.root).issues))


def test_an_approved_part_with_an_obsolete_source_is_flagged(tree):
    tree.standard().write("mpns/OLD.yaml", "mpn: OLD\nmanufacturer: Acme\nlifecycle: obsolete\n")
    tree.part("1102-0002", PART.replace("RC0603FR-0710KL", "OLD"))
    tree.part("1102-0003", PART.replace("RC0603FR-0710KL", "OLD").replace("approved", "draft"))

    flagged = [i for i in load_parts(tree.root).issues if i.kind == "obsolete-mpn"]

    assert [issue.path for issue in flagged] == ["parts/1102/1102-0002.yaml"], "not the draft"


def test_an_approved_part_with_no_source_is_flagged(tree):
    tree.standard().part(
        "1102-0002",
        "ipn: 1102-0002\ndescription: Resistor\nstatus: approved\nsymbol: Passives:R_0603\n",
    )
    assert "no-mpns" in kinds(warnings(load_parts(tree.root).issues))


def test_a_datasheet_is_only_a_link_when_it_is_a_web_address():
    """The value comes out of a repository file and ends up in an href."""
    assert Mpn("X", datasheet="https://example.com/x.pdf").datasheet_url == (
        "https://example.com/x.pdf"
    )
    assert Mpn("X", datasheet="javascript:alert(1)").datasheet_url is None
    assert Mpn("X", datasheet="see the shared drive").datasheet_url is None
    assert Mpn("X").datasheet_url is None


# -- numbering --------------------------------------------------------------


def test_the_category_is_the_part_of_the_number_before_the_dash():
    assert Part("1102-0001").category == "1102"
    assert Part("not an ipn").category == ""


def test_a_number_must_match_its_file(tree):
    tree.standard().write("parts/1102/1102-0009.yaml", PART)  # says it is 1102-0001
    assert "name-mismatch" in kinds(errors(load_parts(tree.root).issues))


def test_a_number_must_be_category_dash_digits(tree):
    tree.standard().write("parts/1102/R-10K.yaml", PART.replace("1102-0001", "R-10K"))
    assert "bad-ipn" in kinds(errors(load_parts(tree.root).issues))


def test_a_category_must_be_declared(tree):
    tree.standard().part("9999-0001")
    assert "unknown-category" in kinds(errors(load_parts(tree.root).issues))


def test_without_a_category_file_parts_still_load(tree):
    tree.write("mpns/RC0603FR-0710KL.yaml", MPN)
    tree.part("1102-0001")

    index = load_parts(tree.root)

    assert "no-categories" in kinds(warnings(index.issues))
    assert errors(index.issues) == []
    assert "1102-0001" in index.parts


def test_a_part_under_the_wrong_category_is_flagged(tree):
    tree.standard().write("parts/1101/1102-0002.yaml", PART.replace("1102-0001", "1102-0002"))
    tree.write("parts/1102-0003.yaml", PART.replace("1102-0001", "1102-0003"))

    misfiled = [i.path for i in load_parts(tree.root).issues if i.kind == "misfiled"]

    assert misfiled == ["parts/1101/1102-0002.yaml"], "directly under parts/ is allowed"


def test_the_same_number_twice_is_an_error(tree):
    tree.standard().write("parts/1101/1102-0001.yaml", PART)
    assert "duplicate-ipn" in kinds(errors(load_parts(tree.root).issues))


# -- what is wrong with a file ----------------------------------------------


def test_a_part_needs_a_symbol_and_wants_a_description(tree):
    tree.standard().part("1102-0002", "ipn: 1102-0002\n")

    index = load_parts(tree.root)

    assert "no-symbol" in kinds(errors(index.issues))
    assert "no-description" in kinds(warnings(index.issues))


def test_a_file_that_is_not_yaml_does_not_stop_the_walk(tree):
    tree.standard().write("parts/1102/broken.yaml", "ipn: [unclosed\n")

    index = load_parts(tree.root)

    assert "1102-0001" in index.parts
    assert "invalid-yaml" in kinds(errors(index.issues))


def test_an_empty_file_is_reported_and_not_passed_over(tree):
    tree.standard().write("parts/1102/1102-0002.yaml", "")
    tree.write("mpns/EMPTY.yaml", "")

    assert {"bad-part", "bad-mpn"} <= kinds(errors(load_parts(tree.root).issues))


def test_malformed_values_are_reported(tree):
    tree.standard().part(
        "1102-0002",
        """\
        ipn: 1102-0002
        description: Resistor
        status: finished
        symbol: Passives:R_0603
        fields: [not, a, mapping]
        mpns: RC0603FR-0710KL
        """,
    )
    tree.write("mpns/ODD.yaml", "mpn: ODD\nlifecycle: retired\n")

    index = load_parts(tree.root)

    assert {"bad-fields", "bad-mpns"} <= kinds(errors(index.issues))
    assert {"bad-status", "bad-lifecycle"} <= kinds(warnings(index.issues))

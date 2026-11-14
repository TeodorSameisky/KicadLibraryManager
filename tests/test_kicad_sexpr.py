"""Reading and writing KiCad's s-expression files."""

import pytest

from app.kicad.sexpr import Atom, ParseError, dumps, loads, quote

# -- reading ----------------------------------------------------------------


def test_parses_a_symbol_library():
    root = loads(
        """(kicad_symbol_lib
            (version 20251024)
            (symbol "R"
                (property "Value" "R")
                (symbol "R_0_1")))"""
    )

    assert root.head == "kicad_symbol_lib"
    assert root.child("version").args == ["20251024"]
    assert root.child("symbol").arg(0) == "R"


def test_quoted_strings_keep_their_spaces_and_escapes():
    root = loads(r'(property "Description" "A \"quoted\" value, with spaces")')
    assert root.args == ["Description", 'A "quoted" value, with spaces']


def test_escaped_whitespace_is_decoded():
    assert loads(r'(a "line1\nline2\tend")').args == ["line1\nline2\tend"]


def test_parentheses_inside_a_string_are_not_structure():
    """A description with brackets in it must not change the nesting."""
    root = loads('(property "Description" "Resistor (thick film) 0603")')
    assert root.arg(1) == "Resistor (thick film) 0603"


def test_bare_tokens_and_strings_are_told_apart():
    """Writing a file back depends on it: (at 0 0 90) but "10k"."""
    root = loads('(property "Value" 10 yes)')

    assert not isinstance(root.args[0], Atom)
    assert isinstance(root.args[1], Atom)
    assert Atom("20251024") == "20251024", "an atom still compares as its text"


def test_children_are_direct_only_and_descendants_are_not():
    root = loads('(a (b "1") (c (b "2")))')

    assert [b.arg(0) for b in root.children("b")] == ["1"]
    assert [b.arg(0) for b in root.descendants("b")] == ["1", "2"]


def test_arg_has_a_default():
    node = loads("(at 1 2)")
    assert node.arg(2) is None
    assert node.arg(2, "0") == "0"


def test_unknown_tokens_pass_through():
    """A token a newer KiCad adds is not an error. That is the whole point."""
    root = loads('(symbol "R" (some_future_token yes) (another (nested 1)))')
    assert root.child("some_future_token").args == ["yes"]


@pytest.mark.parametrize(
    "bad",
    ["(unbalanced", "balanced)", "", "   ", '(unterminated "string', "(one) (two)", "atom"],
    ids=["unclosed", "unopened", "empty", "blank", "open-string", "two-roots", "bare-atom"],
)
def test_malformed_input_is_refused(bad):
    with pytest.raises(ParseError):
        loads(bad)


# -- writing ----------------------------------------------------------------


def test_a_real_kicad_file_round_trips_byte_for_byte():
    """The layout has to be KiCad's, or every served file looks rewritten."""
    original = (
        "(kicad_symbol_lib\n"
        "\t(version 20251024)\n"
        '\t(generator "kicad_symbol_editor")\n'
        '\t(symbol "R"\n'
        "\t\t(pin_numbers\n\t\t\t(hide yes)\n\t\t)\n"
        '\t\t(property "Value" "R"\n'
        "\t\t\t(at 0 0 90)\n"
        "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )

    assert dumps(loads(original)) == original


def test_numbers_stay_bare_and_strings_stay_quoted():
    """Quoting (at 0 0 90) produces a file KiCad rejects."""
    written = dumps(loads('(property "Value" "10k" (at 0 0 90))'))

    assert '(property "Value" "10k"' in written
    assert "(at 0 0 90)" in written


def test_a_list_without_lists_inside_stays_on_one_line():
    assert dumps(loads("(a (b 1 2) (c (d 3)))")) == "(a\n\t(b 1 2)\n\t(c\n\t\t(d 3)\n\t)\n)\n"


def test_a_token_after_a_list_keeps_its_place():
    """Older files write (effects (font ...) hide). Hoisting `hide` in front
    of the font would still parse, but it would no longer be the same file."""
    written = dumps(loads("(effects (font (size 1 1)) hide)"))

    assert written == "(effects\n\t(font\n\t\t(size 1 1)\n\t)\n\thide\n)\n"
    assert dumps(loads(written)) == written


@pytest.mark.parametrize(
    "value,expected",
    [
        ("plain", '"plain"'),
        ('has "quotes"', '"has \\"quotes\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("two\nlines", '"two\\nlines"'),
    ],
)
def test_quoting_escapes_what_it_must(value, expected):
    assert quote(value) == expected
    assert loads(f"(a {expected})").args == [value], "and reads back as it was"


def test_coordinate_lists_share_lines_and_wrap():
    """KiCad packs points onto a line and breaks near column 120."""
    points = " ".join(f"(xy {i}.0254 {i}.3048)" for i in range(12))
    written = dumps(loads(f"(polyline (pts {points}))"))
    lines = [line for line in written.splitlines() if "(xy " in line]

    assert len(lines) > 1, "a long list wraps"
    assert lines[0].count("(xy ") > 1, "points share a line"
    assert max(len(line) for line in lines) < 130
    assert sum(line.count("(xy ") for line in lines) == 12, "no point is lost"


def test_a_short_coordinate_list_stays_on_one_line():
    assert "\t\t(xy 0 0) (xy 1 1)\n" in dumps(loads("(polyline (pts (xy 0 0) (xy 1 1)))"))

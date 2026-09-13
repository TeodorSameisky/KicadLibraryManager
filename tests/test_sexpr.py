"""Parser behaviour, including the cases that matter for KiCad files."""

import pytest

from app.kicad.sexpr import ParseError, loads


def test_parses_a_symbol_library():
    src = '''(kicad_symbol_lib
        (version 20251024)
        (symbol "R"
            (property "Value" "R")
            (symbol "R_0_1")))'''
    root = loads(src)
    assert root.head == "kicad_symbol_lib"
    assert root.child("version").atoms() == ["20251024"]
    assert root.child("symbol").atoms() == ["R"]


def test_quoted_strings_keep_spaces_and_escapes():
    root = loads(r'(property "Description" "A \"quoted\" value, with spaces")')
    assert root.atoms() == ["Description", 'A "quoted" value, with spaces']


def test_escaped_newline_is_decoded():
    root = loads(r'(a "line1\nline2")')
    assert root.head == "a"
    assert root.atoms() == ["line1\nline2"]   # atoms() excludes the head


def test_parentheses_inside_strings_are_not_structure():
    """A datasheet URL or description containing parens must not break nesting."""
    root = loads('(property "Description" "Resistor (thick film) 0603")')
    assert root.atoms()[1] == "Resistor (thick film) 0603"


def test_descendants_finds_nested_matches():
    root = loads('(a (b (c "1")) (d (c "2")))')
    assert [c.atoms()[0] for c in root.descendants("c")] == ["1", "2"]


def test_children_is_direct_only():
    root = loads('(a (b "1") (c (b "2")))')
    assert [b.atoms()[0] for b in root.children("b")] == ["1"]


def test_unknown_tokens_pass_through():
    """Format drift must not be an error: this is the whole point."""
    root = loads('(symbol "R" (some_future_token yes) (another (nested 1)))')
    assert root.child("some_future_token").atoms() == ["yes"]


@pytest.mark.parametrize(
    "bad",
    [
        "(unbalanced",
        "balanced)",
        "",
        '(unterminated "string',
        "(one) (two)",
    ],
)
def test_malformed_input_raises(bad):
    with pytest.raises(ParseError):
        loads(bad)

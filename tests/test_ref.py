"""Parsing a qualified asset reference."""

import pytest

from app.kicad.ref import LibraryRef


def test_a_qualified_reference_splits_on_the_first_colon():
    ref = LibraryRef.parse("Passives:R_0603_1608Metric")

    assert ref == LibraryRef(library="Passives", name="R_0603_1608Metric")
    assert str(ref) == "Passives:R_0603_1608Metric"


def test_only_the_first_colon_separates():
    """A name may contain one; a library prefix may not."""
    assert LibraryRef.parse("Lib:A:B") == LibraryRef(library="Lib", name="A:B")


@pytest.mark.parametrize(
    "raw",
    ["", None, "R_0603", "Passives:", ":R_0603", ":"],
    ids=["empty", "none", "unqualified", "no-name", "no-library", "colon-only"],
)
def test_a_reference_that_names_nothing_is_not_one(raw):
    """Both halves have to be there. A bare name has no library to look in,
    and "Passives:" names nothing -- resolving either would report a missing
    asset and send the reader hunting for a file instead of a typo."""
    assert LibraryRef.parse(raw) is None

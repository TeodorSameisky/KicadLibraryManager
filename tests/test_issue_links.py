"""An issue names a file. What can be opened from the list is a part."""

import shutil

import pytest

from tests.conftest import sign_in, sign_in_librarian
from tests.support import PART

# A repository to push to, and the app over it.
from tests.test_editing import repository, serve  # noqa: F401
from tests.test_manufacturers import LIST, mpn

MISSING_FOOTPRINT = PART.replace("Passives:R_0603_1608Metric", "Passives:Gone")
DRAFT = "ipn: 1102-0003\nstatus: draft\nsymbol: Passives:R\n"


def about(snapshot, kind):
    """What the issue of a kind is about: its manufacturer part, its parts."""
    (issue,) = [issue for issue in snapshot.issues if issue.kind == kind]
    mpn, parts = snapshot.subject(issue)
    return (mpn.mpn if mpn else None, [part.ipn for part in parts])


def test_an_issue_in_a_part_s_file_is_about_that_part(tree):
    tree.standard().part("1102-0002", MISSING_FOOTPRINT.replace("1102-0001", "1102-0002"))

    assert about(tree.snapshot(), "missing-footprint") == (None, ["1102-0002"])


def test_an_issue_in_a_manufacturer_part_s_file_is_about_the_parts_that_list_it(tree):
    tree.standard().write("manufacturers.yaml", LIST)
    tree.write("mpns/RC0603FR-0710KL.yaml", mpn("RC0603FR-0710KL", "YAGEO Corp."))
    tree.part("1102-0002")
    tree.write("mpns/SPARE.yaml", mpn("SPARE", "Nobody Known"))
    snapshot = tree.snapshot()

    assert about(snapshot, "manufacturer-alias") == (
        "RC0603FR-0710KL",
        ["1102-0001", "1102-0002"],
    )
    assert about(snapshot, "unknown-manufacturer") == ("SPARE", []), "listed by no part"


def test_an_issue_about_no_part_is_about_none(tree):
    tree.standard().write("symbols/Passives.kicad_symdir/broken.kicad_sym", "(kicad_symbol_lib")
    tree.write("parts/1102/broken.yaml", "ipn: [unclosed\n")
    snapshot = tree.snapshot()

    assert {issue.path: snapshot.subject(issue) for issue in snapshot.issues if issue.path} == {
        "symbols/Passives.kicad_symdir/broken.kicad_sym": (None, []),
        "parts/1102/broken.yaml": (None, []),
    }


# -- over the API: with whether the part can be changed ---------------------------

git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture()
def site(serve, repository):  # noqa: F811
    """An approved part and a draft, each with something wrong."""
    repository.push_from_elsewhere(
        "parts/1102/1102-0002.yaml", MISSING_FOOTPRINT.replace("1102-0001", "1102-0002")
    )
    repository.push_from_elsewhere("parts/1102/1102-0003.yaml", DRAFT)
    return serve()


def parts_of(client):
    return {
        issue["parts"][0]["ipn"]: issue["parts"][0]["editable"]
        for issue in client.get("/api/v1/issues").json()["issues"]
        if issue["parts"]
    }


@git
def test_a_part_is_offered_for_editing_to_whoever_may_change_it(site):
    assert parts_of(sign_in(site)) == {
        "1102-0002": False,
        "1102-0003": True,
    }, "a draft is anyone's to correct; an approved part is a librarian's"
    assert parts_of(sign_in_librarian(site)) == {"1102-0002": True, "1102-0003": True}


@git
def test_nothing_is_offered_for_editing_where_the_library_cannot_be_written(serve, repository):  # noqa: F811
    repository.push_from_elsewhere("parts/1102/1102-0003.yaml", DRAFT)

    assert parts_of(sign_in_librarian(serve(writable=False))) == {"1102-0003": False}


@git
def test_the_page_links_each_issue_to_its_parts(site):
    script = site.get("/static/js/issues.js").text

    assert "issue.parts" in script and "/edit" in script and "Listed by" in script

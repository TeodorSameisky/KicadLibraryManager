"""Manufacturers: one company under one name, however it was typed."""

import shutil

import pytest

from app.domain.forms import SourceEntry
from app.domain.manufacturers import Manufacturer, Manufacturers, name_key, spelling_groups
from app.domain.policy import Actor
from app.library.issues import errors, warnings
from app.library.parts import load_parts
from app.services import parts
from tests.conftest import sign_in

# A repository to push to, and a library over it.
from tests.test_editing import REQUEST, form, repository, serve, service, web  # noqa: F401

LIST = """\
- name: Texas Instruments
  aliases: [TI, Texas Instr.]
- name: Yageo
- Würth Elektronik
"""

ADA = Actor(name="Ada Lovelace", email="ada@example.com")


def kinds(issues):
    return {issue.kind for issue in issues}


def mpn(number, manufacturer):
    return f"mpn: {number}\nmanufacturer: {manufacturer}\nlifecycle: active\n"


# -- telling that two names are one -------------------------------------------


@pytest.mark.parametrize(
    "written",
    ["Yageo", "YAGEO", "yageo", " Yageo ", "Yageo Corp.", "Yageo Corporation", "Yageo Co., Ltd."],
)
def test_case_punctuation_and_the_legal_form_do_not_make_another_name(written):
    assert name_key(written) == "yageo"


@pytest.mark.parametrize(
    "one,other",
    [
        ("Würth Elektronik", "Wurth Elektronik"),
        ("Würth Elektronik eiSos GmbH & Co. KG", "Würth Elektronik eiSos"),
        ("Analog Devices, Inc.", "analog  devices"),
        ("STMicroelectronics N.V.", "STMicroelectronics"),
        ("Nexperia B.V.", "Nexperia BV"),
        ("Vishay Intertechnology, Inc.", "Vishay Intertechnology Inc"),
        ("Johnson & Johnson", "Johnson and Johnson"),
    ],
)
def test_the_same_name_written_two_ways_is_one_name(one, other):
    assert name_key(one) == name_key(other)


@pytest.mark.parametrize(
    "one,other",
    [
        # What a company does is part of what it is called, unlike how it is
        # incorporated: these are different companies.
        ("ON Semiconductor", "ON"),
        ("Vishay Dale", "Vishay"),
        # And an abbreviation is only known to be one if somebody says so.
        ("TI", "Texas Instruments"),
    ],
)
def test_names_that_differ_in_more_than_that_are_not_assumed_to_be_one(one, other):
    assert name_key(one) != name_key(other)


def test_a_name_that_is_only_a_legal_form_is_still_a_name():
    assert name_key("AG") == "ag"
    assert name_key("") == "" and name_key(" . ") == ""


KNOWN = Manufacturers(
    (
        Manufacturer("Texas Instruments", ("TI", "Texas Instr.")),
        Manufacturer("Yageo"),
    )
)


@pytest.mark.parametrize(
    "written,name",
    [
        ("Texas Instruments", "Texas Instruments"),
        ("texas instruments inc.", "Texas Instruments"),
        ("TI", "Texas Instruments"),
        ("ti", "Texas Instruments"),
        ("YAGEO Corp.", "Yageo"),
        # Not one the library knows: kept as it was typed, tidied of spaces.
        (" Murata ", "Murata"),
        ("", ""),
    ],
)
def test_a_manufacturer_is_called_by_its_one_name_however_it_was_typed(written, name):
    assert KNOWN.canonical(written) == name


def test_a_library_that_keeps_no_list_knows_nobody_and_renames_nothing():
    nobody = Manufacturers()

    assert not nobody and nobody.resolve("Yageo") is None
    assert nobody.canonical("YAGEO Corp.") == "YAGEO Corp."


def test_names_written_more_than_one_way_are_found_the_usual_one_first():
    names = ["Yageo", "YAGEO", "Yageo", "Yageo Corp.", "Murata", "TI", "Texas Instruments", ""]

    assert spelling_groups(names) == [("Yageo", "YAGEO", "Yageo Corp.")]


# -- the library's list ---------------------------------------------------------


def test_the_list_is_read_with_its_other_spellings(tree):
    tree.standard().write("manufacturers.yaml", LIST)

    index = load_parts(tree.root)

    assert index.manufacturers.names == ("Texas Instruments", "Yageo", "Würth Elektronik")
    assert index.manufacturers.resolve("ti").aliases == ("TI", "Texas Instr.")
    assert not index.issues


def test_a_manufacturer_part_is_shown_under_the_one_name(tree):
    tree.standard().write("manufacturers.yaml", LIST)
    tree.write("mpns/LM358.yaml", mpn("LM358", "TI"))
    tree.write("mpns/WE-1.yaml", mpn("WE-1", "Wurth Elektronik GmbH"))

    index = load_parts(tree.root)

    assert index.mpns["LM358"].manufacturer == "Texas Instruments"
    assert index.mpns["WE-1"].manufacturer == "Würth Elektronik"
    assert index.mpns["RC0603FR-0710KL"].manufacturer == "Yageo"


def test_a_name_the_list_does_not_have_is_reported_and_kept(tree):
    tree.standard().write("manufacturers.yaml", LIST)
    tree.write("mpns/GRM1.yaml", mpn("GRM1", "Murata"))

    index = load_parts(tree.root)

    assert index.mpns["GRM1"].manufacturer == "Murata", "as written: the part is still usable"
    (issue,) = [i for i in warnings(index.issues) if i.kind == "unknown-manufacturer"]
    assert issue.path == "mpns/GRM1.yaml"
    assert "'Murata', which is not in manufacturers.yaml" in issue.message


def test_a_manufacturer_part_that_names_nobody_is_not_reported_for_it(tree):
    tree.standard().write("manufacturers.yaml", LIST)
    tree.write("mpns/X.yaml", "mpn: X\n")

    assert "unknown-manufacturer" not in kinds(load_parts(tree.root).issues)


def test_without_a_list_nothing_is_asked_and_nothing_is_renamed(tree):
    tree.standard().write("mpns/GRM1.yaml", mpn("GRM1", "MURATA Mfg. Co., Ltd."))

    index = load_parts(tree.root)

    assert index.mpns["GRM1"].manufacturer == "MURATA Mfg. Co., Ltd."
    assert not index.issues, "a list can be introduced when the library is ready for one"


def test_without_a_list_one_name_written_two_ways_is_still_noticed(tree):
    """Which is where a list starts."""
    tree.standard()
    tree.write("mpns/A.yaml", mpn("A", "YAGEO"))
    tree.write("mpns/B.yaml", mpn("B", "Yageo Corp."))

    (issue,) = warnings(load_parts(tree.root).issues)

    assert issue.kind == "manufacturer-spelling"
    assert issue.message.startswith("'YAGEO' is also written 'Yageo', 'Yageo Corp.'")


def test_a_spelling_can_mean_one_manufacturer_only(tree):
    tree.standard().write(
        "manufacturers.yaml",
        """\
        - name: Texas Instruments
          aliases: [TI]
        - name: Toshiba
          aliases: [TI, Toshiba Corp.]
        - name: Texas Instruments Inc.
        - name: Yageo
        - name: Yageo
        """,
    )

    index = load_parts(tree.root)

    found = [i.message for i in errors(index.issues) if i.kind == "duplicate-manufacturer"]
    assert found == [
        "'TI' is listed for Toshiba, and is already Texas Instruments",
        "'Texas Instruments Inc.' is another way of writing Texas Instruments, "
        "which is already listed",
        "Yageo is listed twice",
    ]
    # The first to claim a spelling keeps it; the rest of each entry stands.
    assert index.manufacturers.names == ("Texas Instruments", "Toshiba", "Yageo")
    assert index.manufacturers.canonical("TI") == "Texas Instruments"
    assert index.manufacturers.canonical("toshiba corporation") == "Toshiba"


@pytest.mark.parametrize(
    "text,kind",
    [
        ("name: Yageo\n", "bad-manufacturers"),
        ("- aliases: [TI]\n", "bad-manufacturer"),
        ("- name: Yageo\n  aliases: YAGEO\n", "bad-manufacturer"),
        ("- [unclosed\n", "invalid-yaml"),
    ],
    ids=["not-a-list", "no-name", "aliases-not-a-list", "not-yaml"],
)
def test_a_list_that_is_wrong_is_said_and_the_library_still_loads(tree, text, kind):
    tree.standard().write("manufacturers.yaml", text)

    index = load_parts(tree.root)

    assert kind in kinds(errors(index.issues))
    assert "1102-0001" in index.parts


# -- finding parts --------------------------------------------------------------


def test_a_part_is_found_by_any_way_its_manufacturer_is_written(tree):
    tree.standard().write("manufacturers.yaml", "- name: Yageo\n  aliases: [Phycomp]\n")

    snapshot = tree.snapshot()

    assert [part.ipn for part in snapshot.search("phycomp")] == ["1102-0001"]
    assert [part.ipn for part in snapshot.search("yageo")] == ["1102-0001"]
    assert snapshot.search("murata") == []


# -- asking for a part ----------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@needs_git
async def test_a_new_manufacturer_part_is_written_down_under_the_one_name(service, repository):  # noqa: F811
    repository.push_from_elsewhere("manufacturers.yaml", LIST)
    await service.refresh()
    asked = form(
        sources=(
            SourceEntry("LM358", manufacturer="ti", preferred=True),
            SourceEntry("GRM1", manufacturer=" Murata "),
        )
    )

    ipn = await parts.request_part(service, asked, actor=ADA)

    assert "manufacturer: Texas Instruments" in repository.show("mpns/LM358.yaml")
    # Not one the library knows: kept as typed, and said on the issues page.
    assert "manufacturer: Murata" in repository.show("mpns/GRM1.yaml")
    assert service.snapshot.part(ipn) is not None
    unknown = [i.message for i in service.snapshot.warnings if i.kind == "unknown-manufacturer"]
    assert unknown == ["GRM1 is made by 'Murata', which is not in manufacturers.yaml"]


@needs_git
def test_the_form_is_offered_the_manufacturers_the_library_has(serve, repository):  # noqa: F811
    repository.push_from_elsewhere("manufacturers.yaml", LIST)
    repository.push_from_elsewhere("mpns/GRM1.yaml", mpn("GRM1", "murata"))
    client = sign_in(serve())

    offered = client.get("/api/v1/library/choices").json()["manufacturers"]

    # The list's names, and whoever a manufacturer part already names.
    assert offered == ["murata", "Texas Instruments", "Würth Elektronik", "Yageo"]


@needs_git
def test_without_a_list_the_form_offers_whoever_is_already_named(web):  # noqa: F811
    assert web.get("/api/v1/library/choices").json()["manufacturers"] == ["Yageo"]


@needs_git
def test_the_form_page_has_the_list_for_its_manufacturer_field(web):  # noqa: F811
    page = web.get("/parts/new").text
    script = web.get("/static/js/part-form.js").text

    assert '<datalist id="manufacturers"></datalist>' in page
    assert 'manufacturer.setAttribute("list", "manufacturers")' in script
    assert 'fill("manufacturers", offered.manufacturers)' in script

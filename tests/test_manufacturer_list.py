"""Keeping the list of manufacturers: the worklist, the form, and the commits."""

import json
import re
import shutil

import pytest

from app.domain.forms import ManufacturerForm
from app.domain.policy import Actor
from app.errors import NotAllowed, NotFound
from app.library import manufacturer_editing as editing
from app.library.editing import EditError
from app.services import manufacturers
from tests.conftest import sign_in, sign_in_librarian

# A repository to push to, and a library over it.
from tests.test_editing import repository, serve, service  # noqa: F401
from tests.test_manufacturers import LIST, mpn

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ADA = Actor(name="Ada Lovelace", email="ada@example.com")
GRACE = Actor(librarian=True, name="Grace Hopper", email="grace@example.com")


def listing(tree, text=LIST, **made_by):
    """A library with a list, and a manufacturer part for each name given."""
    tree.standard()
    if text is not None:
        tree.write("manufacturers.yaml", text)
    for number, manufacturer in made_by.items():
        tree.write(f"mpns/{number}.yaml", mpn(number, manufacturer))
    return tree.snapshot()


# -- the worklist ---------------------------------------------------------------


def test_the_list_says_how_much_each_manufacturer_makes(tree):
    snapshot = listing(tree, A="TI", B="Texas Instruments Inc.")

    listed = manufacturers.overview(snapshot).listed

    assert [(entry.manufacturer.name, entry.parts) for entry in listed] == [
        ("Texas Instruments", 2),
        ("Würth Elektronik", 0),
        ("Yageo", 1),
    ]


def test_names_the_list_does_not_have_are_the_work_to_do_the_most_used_first(tree):
    snapshot = listing(tree, A="Murata", B="MURATA", C="murata", D="Murata", E="Kemet")

    unlisted = manufacturers.overview(snapshot).unlisted

    assert [(found.name, found.spellings, found.parts) for found in unlisted] == [
        ("Murata", ("Murata", "MURATA", "murata"), 4),
        ("Kemet", ("Kemet",), 1),
    ]


def test_a_library_with_no_list_has_everyone_still_to_be_listed(tree):
    overview = manufacturers.overview(listing(tree, text=None))

    assert overview.listed == ()
    assert [found.name for found in overview.unlisted] == ["Yageo"]


# -- whether a request makes sense ----------------------------------------------


@pytest.mark.parametrize(
    "asked,said",
    [
        (ManufacturerForm(" "), "Give the manufacturer a name."),
        (ManufacturerForm("..."), "The name needs a letter or a digit in it."),
        (ManufacturerForm("Yageo"), "Yageo is already in the list."),
        (
            ManufacturerForm("YAGEO Corp."),
            "YAGEO Corp. is another way of writing Yageo, which is already listed.",
        ),
        (ManufacturerForm("ti"), "ti is another way of writing Texas Instruments"),
        (ManufacturerForm("Toshiba", ("TI",)), "TI is already a way of writing Texas Instruments."),
        (ManufacturerForm("Murata", tuple(f"M{n}" for n in range(41))), "at most 40 other"),
        (ManufacturerForm("M" * 121), "at most 120 characters"),
    ],
    ids=["no-name", "no-letters", "listed", "same-name", "an-alias", "alias-taken", "many", "long"],
)
def test_what_is_wrong_with_an_entry_is_said(tree, asked, said):
    with pytest.raises(EditError, match=re.escape(said)):
        manufacturers.check_manufacturer(asked, listing(tree))


def test_everything_wrong_is_said_at_once(tree):
    with pytest.raises(EditError) as refused:
        manufacturers.check_manufacturer(ManufacturerForm("Yageo", ("TI",)), listing(tree))

    assert len(refused.value.problems) == 2


def test_an_entry_does_not_collide_with_itself(tree):
    snapshot = listing(tree)

    manufacturers.check_manufacturer(
        ManufacturerForm("Texas Instruments", ("TI", "Burr-Brown")), snapshot, "Texas Instruments"
    )
    manufacturers.check_manufacturer(ManufacturerForm("TI"), snapshot, "Texas Instruments")
    with pytest.raises(EditError, match="Yageo is already in the list"):
        manufacturers.check_manufacturer(ManufacturerForm("Yageo"), snapshot, "Texas Instruments")


# -- the file -------------------------------------------------------------------


def test_the_first_manufacturer_starts_the_list(tree):
    tree.standard()

    subject = editing.write_manufacturer(tree.root, None, "Murata", ("muRata",))

    assert subject == "Add manufacturer Murata"
    assert (tree.root / "manufacturers.yaml").read_text(encoding="utf-8") == (
        "- name: Murata\n  aliases: [muRata]\n"
    )


def test_a_list_kept_in_order_stays_in_order(tree):
    tree.standard().write("manufacturers.yaml", "- Kemet\n- name: Yageo\n")

    editing.write_manufacturer(tree.root, None, "murata", ())

    assert (tree.root / "manufacturers.yaml").read_text(encoding="utf-8") == (
        "- name: Kemet\n- name: murata\n- name: Yageo\n"
    )


def test_somebody_s_own_order_is_left_alone_and_what_else_an_entry_holds_is_kept(tree):
    tree.standard().write(
        "manufacturers.yaml",
        """\
        - name: Yageo
          website: https://www.yageo.com
        - name: Kemet
          aliases: [KEMET Electronics]
        """,
    )

    editing.write_manufacturer(tree.root, None, "Murata", ())
    subject = editing.write_manufacturer(tree.root, "Yageo", "Yageo", ("Phycomp", "0805"))

    assert subject == "Change manufacturer Yageo"
    assert (tree.root / "manufacturers.yaml").read_text(encoding="utf-8") == (
        "- name: Yageo\n"
        "  aliases: [Phycomp, '0805']\n"
        "  website: https://www.yageo.com\n"
        "- name: Kemet\n"
        "  aliases: [KEMET Electronics]\n"
        "- name: Murata\n"
    )


def test_an_entry_is_renamed_emptied_and_removed(tree):
    tree.standard().write("manufacturers.yaml", LIST)

    assert editing.write_manufacturer(tree.root, "Texas Instruments", "TI Inc", ()) == (
        "Rename manufacturer Texas Instruments to TI Inc"
    )
    assert editing.delete_manufacturer(tree.root, "Yageo") == "Remove manufacturer Yageo"
    assert (tree.root / "manufacturers.yaml").read_text(encoding="utf-8") == (
        "- name: TI Inc\n- name: Würth Elektronik\n"
    )

    assert editing.delete_manufacturer(tree.root, "TI Inc")
    assert editing.delete_manufacturer(tree.root, "Würth Elektronik")
    assert (tree.root / "manufacturers.yaml").read_text(encoding="utf-8") == "[]\n"
    with pytest.raises(EditError, match="There is no manufacturer Yageo in the list"):
        editing.delete_manufacturer(tree.root, "Yageo")


def test_a_file_that_is_not_a_list_is_not_written_over(tree):
    tree.standard().write("manufacturers.yaml", "name: Yageo\n")

    with pytest.raises(EditError, match="cannot be read as a list"):
        editing.write_manufacturer(tree.root, None, "Murata", ())

    assert (tree.root / "manufacturers.yaml").read_text(encoding="utf-8") == "name: Yageo\n"


# -- as commits -----------------------------------------------------------------


@pytest.fixture()
async def library(service, repository):  # noqa: F811
    repository.push_from_elsewhere("manufacturers.yaml", LIST)
    repository.push_from_elsewhere("mpns/GRM1.yaml", mpn("GRM1", "muRata"))
    await service.refresh()
    return service


async def test_a_librarian_lists_a_manufacturer_and_the_worklist_shrinks(library, repository):  # noqa: F811
    before = manufacturers.overview(library.snapshot)
    assert [found.name for found in before.unlisted] == ["muRata"]

    after = await manufacturers.save_manufacturer(
        library, ManufacturerForm("Murata"), note="From the capacitor datasheets.", actor=GRACE
    )

    assert after.unlisted == ()
    assert library.snapshot.mpn("GRM1").manufacturer == "Murata", "shown under the one name"
    assert "manufacturer: muRata" in repository.show("mpns/GRM1.yaml"), "the file is not rewritten"
    assert repository.last("%an") == "Grace Hopper"
    assert repository.last("%B").splitlines() == [
        "Add manufacturer Murata",
        "",
        "From the capacitor datasheets.",
    ]


async def test_a_renamed_manufacturer_is_still_found_by_its_old_name(library, repository):  # noqa: F811
    """The files that say the old name are not rewritten. Without this every
    one of them would name nobody from then on."""
    after = await manufacturers.save_manufacturer(
        library, ManufacturerForm("YAGEO Group", ("Phycomp",)), was="Yageo", actor=GRACE
    )

    assert "- name: YAGEO Group\n  aliases: [Phycomp, Yageo]\n" in repository.show(
        "manufacturers.yaml"
    )
    assert repository.last("%s") == "Rename manufacturer Yageo to YAGEO Group"
    assert library.snapshot.mpn("RC0603FR-0710KL").manufacturer == "YAGEO Group"
    assert [found.name for found in after.unlisted] == ["muRata"]


async def test_a_spelling_that_the_name_already_covers_is_not_written_down(library, repository):  # noqa: F811
    await manufacturers.save_manufacturer(
        library,
        ManufacturerForm("Murata", ("MURATA Mfg.", "murata", " ", "Murata Mfg")),
        actor=GRACE,
    )

    # The last entry, and the one spelling the name does not already cover.
    written = repository.show("manufacturers.yaml")
    assert written.endswith("- name: Murata\n  aliases: [MURATA Mfg.]")


async def test_a_manufacturer_taken_off_the_list_is_back_on_the_worklist(library, repository):  # noqa: F811
    after = await manufacturers.remove_manufacturer(library, "Yageo", actor=GRACE)

    assert repository.last("%s") == "Remove manufacturer Yageo"
    assert sorted(found.name for found in after.unlisted) == ["Yageo", "muRata"]


async def test_the_list_is_a_librarian_s_to_keep(library, repository):  # noqa: F811
    before = repository.commits()

    with pytest.raises(NotAllowed):
        await manufacturers.save_manufacturer(library, ManufacturerForm("Murata"), actor=ADA)
    with pytest.raises(NotAllowed):
        await manufacturers.remove_manufacturer(library, "Yageo", actor=ADA)
    with pytest.raises(NotFound, match="There is no manufacturer Kemet in the list"):
        await manufacturers.save_manufacturer(
            library, ManufacturerForm("Kemet"), was="Kemet", actor=GRACE
        )
    with pytest.raises(NotFound):
        await manufacturers.remove_manufacturer(library, "Kemet", actor=GRACE)
    with pytest.raises(EditError):
        await manufacturers.save_manufacturer(library, ManufacturerForm("Yageo"), actor=GRACE)

    assert repository.commits() == before


# -- through the web ------------------------------------------------------------


@pytest.fixture()
def site(serve, repository):  # noqa: F811
    """The app over a library with a list and one name that is not on it."""
    repository.push_from_elsewhere("manufacturers.yaml", LIST)
    repository.push_from_elsewhere("mpns/GRM1.yaml", mpn("GRM1", "muRata"))
    return serve()


def test_anyone_signed_in_reads_the_list_and_what_is_not_on_it(site):
    answer = sign_in(site).get("/api/v1/manufacturers").json()

    assert answer["writable"] is True
    assert answer["manufacturers"][0] == {
        "name": "Texas Instruments",
        "aliases": ["TI", "Texas Instr."],
        "parts": 0,
    }
    assert answer["unlisted"] == [{"name": "muRata", "spellings": ["muRata"], "parts": 1}]


def test_a_librarian_changes_the_list_over_the_api(site, repository):  # noqa: F811
    grace = sign_in_librarian(site)

    added = grace.put("/api/v1/manufacturers", json={"name": "Murata", "note": "Listed."})
    assert added.status_code == 200 and added.json()["unlisted"] == []
    assert repository.last("%B").splitlines() == ["Add manufacturer Murata", "", "Listed."]

    changed = grace.put(
        "/api/v1/manufacturers", json={"was": "Murata", "name": "Murata", "aliases": ["MuRata Mfg"]}
    )
    assert changed.status_code == 200, "its own name is not somebody else's"

    refused = grace.put("/api/v1/manufacturers", json={"name": "TI"})
    assert refused.status_code == 422
    assert refused.json()["problems"] == [
        "TI is another way of writing Texas Instruments, which is already listed."
    ]

    removed = grace.request("DELETE", "/api/v1/manufacturers", json={"name": "Murata"})
    assert removed.status_code == 200
    assert [found["name"] for found in removed.json()["unlisted"]] == ["muRata"]
    assert (
        grace.request("DELETE", "/api/v1/manufacturers", json={"name": "Murata"}).status_code == 404
    )


def test_someone_who_is_not_a_librarian_reads_and_does_not_change(site, repository):  # noqa: F811
    ada = sign_in(site)
    before = repository.commits()

    assert ada.put("/api/v1/manufacturers", json={"name": "Murata"}).status_code == 403
    assert ada.request("DELETE", "/api/v1/manufacturers", json={"name": "Yageo"}).status_code == 403
    for page in ("/manufacturers", "/manufacturers/new", "/manufacturers/edit?name=Yageo"):
        assert ada.get(page, headers={"accept": "text/html"}).status_code == 403, page
    assert "/manufacturers" not in ada.get("/panel").text, "and is not offered the page"

    assert repository.commits() == before


def test_the_page_is_the_worklist_and_the_list(site):
    page = sign_in_librarian(site).get("/manufacturers").text

    assert "Not in the list" in page and "<strong>muRata</strong>" in page
    # Either a manufacturer to add, or another way of writing one that is listed.
    assert 'href="/manufacturers/new?name=muRata"' in page
    assert '<input type="hidden" name="alias" value="muRata" />' in page
    assert '<option value="Texas Instruments">' in page
    # And the list, with a way into each entry.
    assert 'href="/manufacturers/edit?name=W%C3%BCrth%20Elektronik"' in page
    assert "TI, Texas Instr." in page
    assert 'href="/manufacturers"' in page and 'aria-current="page">Manufacturers' in page


def data_of(page):
    """What the form's script is handed to start from."""
    found = re.search(
        r'<script type="application/json" id="manufacturer-data">(.*?)</script>', page
    )
    return json.loads(found.group(1))


def test_the_form_starts_from_what_the_worklist_sent(site):
    grace = sign_in_librarian(site)

    new = grace.get("/manufacturers/new?name=muRata").text
    assert data_of(new) == {"was": None, "name": "muRata", "aliases": []}
    assert "Add the manufacturer" in new and "manufacturer-remove" not in new

    # "It is this one": the spelling is in the form, and not yet in the list.
    edit = grace.get("/manufacturers/edit?name=Texas%20Instruments&alias=muRata").text
    assert data_of(edit) == {
        "was": "Texas Instruments",
        "name": "Texas Instruments",
        "aliases": ["TI", "Texas Instr.", "muRata"],
    }
    assert "Save the manufacturer" in edit and 'id="manufacturer-remove"' in edit
    assert 'type="module" src="/static/js/manufacturer-form.js"' in edit

    # One the entry already covers is not added a second time.
    again = grace.get("/manufacturers/edit?name=Texas%20Instruments&alias=ti").text
    assert data_of(again)["aliases"] == ["TI", "Texas Instr."]

    assert (
        grace.get("/manufacturers/edit?name=Nobody", headers={"accept": "text/html"}).status_code
        == 404
    )


def test_a_library_that_cannot_be_written_shows_the_list_and_offers_nothing(serve, repository):  # noqa: F811
    repository.push_from_elsewhere("manufacturers.yaml", LIST)
    grace = sign_in_librarian(serve(writable=False))

    assert grace.put("/api/v1/manufacturers", json={"name": "Murata"}).status_code == 503
    # The pages are reached by address: the navigation no longer offers them.
    page = grace.get("/manufacturers").text
    assert "Texas Instruments" in page and "cannot change the library" in page
    assert "/manufacturers/new" not in page and "/manufacturers/edit" not in page
    assert "manufacturer-form.js" not in grace.get("/manufacturers/new").text

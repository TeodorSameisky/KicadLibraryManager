"""What can be done with the library, asked without HTTP.

The same calls the routes make, made directly: who may do each thing, what is
refused and as which kind of error, and what comes of it. What an error is
over HTTP is decided in one table, checked at the end.
"""

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import errors
from app.auth.identity import Principal
from app.auth.roles import actor_for
from app.boms.parse import BomError
from app.boms.store import BomExists, BomStore, StoreUnavailable
from app.domain.policy import Actor
from app.domain.templates import TemplateField
from app.errors import AppError, Invalid, NotAllowed, NotFound, Unavailable
from app.library.editing import EditError
from app.library.service import LibraryService
from app.library.template_editing import TemplateForm
from app.services import boms, changes, parts, templates
from app.services.boms import BomUpload
from app.web.errors import _STATUS, app_error
from tests.conftest import settings_for

# A repository to push to, and a library over it.
from tests.test_boms import KICAD_DEFAULT
from tests.test_editing import form, repository, service  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ADA = Actor(name="Ada Lovelace", email="ada@example.com")
GRACE = Actor(librarian=True, name="Grace Hopper", email="grace@example.com")


@pytest.fixture()
async def library(service):  # noqa: F811
    await service.refresh()
    return service


# -- who is asking ------------------------------------------------------------


def test_someone_signed_in_is_known_by_what_the_provider_says_of_them():
    ada = Principal("user-1", {"name": "Ada Lovelace", "email": "ada@example.com"})

    actor = actor_for(ada, ("ada@example.com",), auth_configured=True, hostname="lib.example.com")

    assert actor == Actor(librarian=True, name="Ada Lovelace", email="ada@example.com")


def test_someone_without_an_address_is_given_one_that_receives_nothing():
    """Which is what git hosts do, for the same reason."""
    someone = Principal("0123456789abcdef", {"preferred_username": "ada l"})
    nobody = Principal("0123456789abcdef")

    assert someone.address("lib.example.com") == "ada-l@users.noreply.lib.example.com"
    assert nobody.address("lib.example.com") == "0123456789ab@users.noreply.lib.example.com"


def test_with_sign_in_off_changes_are_nobody_s_and_everyone_is_a_librarian():
    actor = actor_for(None, (), auth_configured=False, hostname="lib.example.com")

    assert actor == Actor(librarian=True, name="", email="nobody@users.noreply.lib.example.com")
    assert changes.author_of(actor).name == "Someone"


def test_a_name_cannot_break_the_commit_it_goes_into():
    author = changes.author_of(Actor(name="Ada <ada@evil.example>\nLovelace", email="a@b.example"))

    assert author.name == "Ada ada@evil.exampleLovelace"


# -- parts ----------------------------------------------------------------------


async def test_anyone_requests_a_part_and_it_is_a_draft_in_their_name(library, repository):  # noqa: F811
    ipn = await parts.request_part(library, form(), note="For the new amplifier.", actor=ADA)

    assert ipn == "1102-0002"
    assert library.snapshot.part(ipn).status == "draft"
    assert repository.last("%an <%ae>") == "Ada Lovelace <ada@example.com>"
    assert repository.last("%B").splitlines() == [
        "Request 1102-0002: Resistor 330R 1% 0.1W 0603",
        "",
        "For the new amplifier.",
    ]


async def test_a_request_that_makes_no_sense_is_refused_with_everything_wrong(library, repository):  # noqa: F811
    before = repository.commits()

    with pytest.raises(EditError) as refused:
        await parts.request_part(library, form(description="", symbol=""), actor=ADA)

    assert isinstance(refused.value, Invalid)
    assert len(refused.value.problems) == 2
    assert repository.commits() == before


async def test_a_draft_is_anyone_s_to_correct_and_the_rest_is_a_librarian_s(library, repository):  # noqa: F811
    draft = await parts.request_part(library, form(), actor=ADA)
    approved = library.snapshot.part("1102-0001")

    await parts.edit_part(
        library, library.snapshot.part(draft), form(description="Corrected"), actor=ADA
    )
    assert library.snapshot.part(draft).description == "Corrected"

    before = repository.commits()
    with pytest.raises(NotAllowed, match="Only a librarian can change"):
        await parts.edit_part(library, approved, form(description="Mine now"), actor=ADA)
    assert repository.commits() == before

    await parts.edit_part(library, approved, form(description="Hers to change"), actor=GRACE)
    assert library.snapshot.part("1102-0001").description == "Hers to change"


async def test_only_a_librarian_decides(library, repository):  # noqa: F811
    ipn = await parts.request_part(library, form(), actor=ADA)
    draft = library.snapshot.part(ipn)

    with pytest.raises(NotAllowed):
        await parts.set_status(library, draft, "approved", actor=ADA)
    assert library.snapshot.part(draft.ipn).status == "draft"

    await parts.set_status(library, draft, "approved", note="Checked.", actor=GRACE)
    assert library.snapshot.part(draft.ipn).status == "approved"
    assert repository.last("%an") == "Grace Hopper"


async def test_a_library_that_cannot_be_written_says_so_before_anything_else(repository, tmp_path):  # noqa: F811
    """With no way to save a change, saying what else is wrong with it helps nobody."""
    read_only = LibraryService([repository.source], tmp_path / "work")
    await read_only.refresh()
    part = read_only.snapshot.part("1102-0001")

    for attempt in (
        parts.request_part(read_only, form(description=""), actor=ADA),
        parts.edit_part(read_only, part, form(), actor=ADA),
        parts.set_status(read_only, part, "obsolete", actor=ADA),
    ):
        with pytest.raises(changes.NotWritable) as refused:
            await attempt
        assert isinstance(refused.value, Unavailable)


# -- templates ------------------------------------------------------------------


async def test_a_template_is_a_librarian_s_to_change(library, repository):  # noqa: F811
    before = repository.commits()

    with pytest.raises(NotAllowed):
        await templates.save_template(library, "1102", TemplateForm(), actor=ADA)
    with pytest.raises(NotAllowed):
        await templates.remove_template(library, "1102", actor=ADA)
    with pytest.raises(NotFound, match="There is no category 9999"):
        await templates.save_template(library, "9999", TemplateForm(), actor=GRACE)

    assert repository.commits() == before


async def test_saving_a_template_says_what_it_leaves_the_parts_short_of(library, repository):  # noqa: F811
    asked = TemplateForm(fields=(TemplateField("Voltage", required=True),))

    short = await templates.save_template(library, "1102", asked, note="Derating.", actor=GRACE)

    assert (short.parts, short.of) == (1, 1), "the one resistor says nothing of its voltage"
    assert "Voltage" in repository.show("templates/1102.yaml")

    short = await templates.remove_template(library, "1102", actor=GRACE)
    assert (short.parts, short.of) == (0, 1)


# -- BOMs -----------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return BomStore(tmp_path / "library-manager.db")


def test_a_bom_is_kept_under_who_uploaded_it(store, tree):
    snapshot = tree.standard().snapshot()

    bom, warnings = boms.upload_bom(
        store, snapshot, BomUpload(project="  Amplifier  ", content=KICAD_DEFAULT), actor=ADA
    )

    assert (bom.project, bom.uploaded_by, warnings) == ("Amplifier", "Ada Lovelace", ())
    assert boms.get_bom(store, bom.id).line_count == bom.line_count


@pytest.mark.parametrize(
    "upload,kind,said",
    [
        (BomUpload(project="   ", content=KICAD_DEFAULT), Invalid, "needs a name"),
        (BomUpload(project="A", content=KICAD_DEFAULT, device="akd/none"), Invalid, "no device"),
        (BomUpload(project="A", content="not,a,bom\n1,2,3\n"), BomError, ""),
    ],
    ids=["no-project", "unknown-device", "not-a-bom"],
)
def test_an_upload_that_makes_no_sense_is_refused(store, tree, upload, kind, said):
    with pytest.raises(kind, match=said) as refused:
        boms.upload_bom(store, tree.standard().snapshot(), upload, actor=ADA)

    assert isinstance(refused.value, Invalid)
    assert store.list() == []


def test_the_same_revision_twice_has_to_be_chosen(store, tree):
    snapshot = tree.standard().snapshot()
    upload = BomUpload(project="Amplifier", revision="A", content=KICAD_DEFAULT)
    first, _ = boms.upload_bom(store, snapshot, upload, actor=ADA)

    with pytest.raises(BomExists) as refused:
        boms.upload_bom(store, snapshot, upload, actor=ADA)
    assert refused.value.existing.id == first.id

    again = BomUpload(project="Amplifier", revision="A", content=KICAD_DEFAULT, replace=True)
    boms.upload_bom(store, snapshot, again, actor=ADA)
    assert len(store.list()) == 1


def test_only_a_librarian_throws_a_bom_away(store, tree):
    bom, _ = boms.upload_bom(
        store, tree.standard().snapshot(), BomUpload(project="A", content=KICAD_DEFAULT), actor=ADA
    )

    with pytest.raises(NotAllowed):
        boms.delete_bom(store, bom.id, actor=ADA)
    assert store.get(bom.id) is not None

    boms.delete_bom(store, bom.id, actor=GRACE)
    with pytest.raises(NotFound):
        boms.get_bom(store, bom.id)
    with pytest.raises(NotFound):
        boms.delete_bom(store, bom.id, actor=GRACE)


# -- what an error is over HTTP ---------------------------------------------------


def test_every_kind_of_error_has_a_status():
    """A kind added to `app.errors` and not to the table would be answered 500."""
    kinds = {
        kind
        for kind in vars(errors).values()
        if isinstance(kind, type) and issubclass(kind, AppError) and kind is not AppError
    }

    assert kinds == {kind for kind, _ in _STATUS}


@pytest.fixture()
def answering():
    """An app with one route, which raises whatever it is given."""
    app = FastAPI()
    app.state.settings = settings_for()
    app.add_exception_handler(AppError, app_error)
    raised: list[AppError] = []

    @app.get("/")
    def fail() -> None:
        raise raised[0]

    def ask(error: AppError):
        raised[:] = [error]
        return TestClient(app).get("/")

    return ask


@pytest.mark.parametrize(
    "error,status",
    [
        (errors.NotFound("There is no part 9999-0001"), 404),
        (errors.NotAllowed("Only a librarian can do this."), 403),
        (errors.Invalid("The project needs a name."), 422),
        (errors.Conflict("1102-0001 cannot be placed"), 409),
        (errors.Unavailable("Not now."), 503),
        (errors.UpstreamFailed("The git host said no."), 502),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, AppError) else "",
)
def test_an_error_is_answered_by_its_kind_wherever_it_was_raised(answering, error, status):
    answer = answering(error)

    assert answer.status_code == status
    assert answer.json() == {"detail": str(error)}


def test_an_error_that_found_several_things_lists_them(answering):
    answer = answering(EditError(["Choose a category.", "Choose a symbol."]))

    assert answer.status_code == 422
    assert answer.json() == {
        "detail": "Choose a category. Choose a symbol.",
        "problems": ["Choose a category.", "Choose a symbol."],
    }


def test_what_is_meant_for_the_log_is_not_in_the_answer(answering):
    answer = answering(StoreUnavailable("the directory does not exist: /data/secret"))

    assert answer.status_code == 503
    assert answer.json() == {"detail": "BOMs cannot be stored or read right now."}

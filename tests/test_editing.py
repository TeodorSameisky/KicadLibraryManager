"""Requesting, correcting and approving parts -- as commits to a real repository."""

import shutil
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from app.application import create_app
from app.boms.store import BomStore
from app.domain.forms import PartForm, SourceEntry
from app.library import editing
from app.library.editing import EditError
from app.library.files import mpn_file_stem
from app.library.parts import load_parts
from app.library.publish import Identity, Publisher, PublishError
from app.library.service import LibraryService
from app.library.sources import Source
from app.services import parts
from tests.conftest import OPEN, settings_for, sign_in, sign_in_librarian, wait_until_indexed
from tests.support import PART, LibraryTree

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ADA = Identity("Ada Lovelace", "ada@example.com")
APP = Identity("KiCad Library Manager", "noreply@library.example.com")


def git(*args, cwd=None):
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def form(**changes) -> PartForm:
    values = {
        "category": "1102",
        "description": "Resistor 330R 1% 0.1W 0603",
        "symbol": "Passives:R_0603",
        "footprint": "Passives:R_0603_1608Metric",
        "fields": {"Value": "330R"},
        "sources": (SourceEntry("RC0603FR-0710KL", preferred=True),),
    }
    return PartForm(**{**values, **changes})


# -- deciding whether a request makes sense ---------------------------------


@pytest.fixture()
def snapshot(tree):
    return tree.standard().snapshot()


def problems(form, snapshot, new=True):
    with pytest.raises(EditError) as refused:
        parts.check(form, snapshot, new=new)
    return refused.value.problems


def test_a_sensible_request_is_accepted(snapshot):
    parts.check(form(), snapshot, new=True)
    parts.check(form(category=""), snapshot, new=False), "an existing part keeps its category"


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"category": "9999"}, "Choose a category."),
        ({"description": "   "}, "Give the part a description"),
        ({"symbol": ""}, "Choose a symbol."),
        ({"symbol": "Passives:Ghost"}, "The library has no symbol Passives:Ghost."),
        ({"footprint": "Passives:Ghost"}, "The library has no footprint Passives:Ghost."),
        ({"fields": {" ": "x"}}, "Every field needs a name."),
        ({"sources": (SourceEntry(" "),)}, "Every manufacturer part needs its part number."),
        ({"sources": (SourceEntry("A"), SourceEntry("A"))}, "listed twice"),
        (
            {"sources": (SourceEntry("A", preferred=True), SourceEntry("B", preferred=True))},
            "Only one manufacturer part can be the preferred one.",
        ),
        ({"sources": (SourceEntry("NEW", lifecycle="retired"),)}, "NEW: the lifecycle must be"),
        (
            {"sources": (SourceEntry("NEW", datasheet="javascript:alert(1)"),)},
            "NEW: the datasheet must be a web address.",
        ),
    ],
)
def test_what_is_wrong_with_a_request_is_said(snapshot, changes, expected):
    assert any(expected in problem for problem in problems(form(**changes), snapshot))


def test_everything_wrong_is_said_at_once(snapshot):
    """Not one problem per attempt."""
    found = problems(form(description="", symbol="", category=""), snapshot)
    assert len(found) == 3


def test_a_part_may_have_no_footprint_and_no_sources(snapshot):
    parts.check(form(footprint="", sources=()), snapshot, new=True)


def test_what_the_form_says_about_a_known_manufacturer_part_is_not_checked(snapshot):
    """It is not used either: one manufacturer part serves many parts."""
    known = SourceEntry("RC0603FR-0710KL", lifecycle="nonsense", datasheet="not a url")
    parts.check(form(sources=(known,)), snapshot, new=True)


# -- writing the files ------------------------------------------------------


def test_a_new_part_takes_the_next_number_in_its_category(tree):
    tree.standard().part("1102-0007")

    assert editing.next_ipn(tree.root, "1102") == "1102-0008"
    assert editing.next_ipn(tree.root, "1101") == "1101-0001", "the first in an empty category"


def test_a_new_part_is_written_as_a_draft_the_library_can_read(tree):
    tree.standard()

    ipn, message = editing.write_new_part(
        tree.root, form(fields={"Value": "330R", "Tolerance": "1%"})
    )

    assert (ipn, message) == ("1102-0002", "Request 1102-0002: Resistor 330R 1% 0.1W 0603")
    text = (tree.root / "parts/1102/1102-0002.yaml").read_text(encoding="utf-8")
    assert text == (
        "ipn: 1102-0002\n"
        "description: Resistor 330R 1% 0.1W 0603\n"
        "status: draft\n"
        "\n"
        "symbol: Passives:R_0603\n"
        "footprint: Passives:R_0603_1608Metric\n"
        "\n"
        "fields:\n"
        "  Value: 330R\n"
        "  Tolerance: 1%\n"
        "\n"
        "mpns:\n"
        "- mpn: RC0603FR-0710KL\n"
        "  preferred: true\n"
    )
    index = load_parts(tree.root)
    # Read back without a fault. What is said of it is that it prefers what
    # 1102-0001 prefers, which the standard library's one MPN makes it do.
    assert [issue.kind for issue in index.issues] == ["duplicate-part"]
    assert index.parts["1102-0002"].status == "draft"


def test_a_value_that_looks_like_a_number_stays_text(tree):
    """`100` would come back from YAML as a number, and KiCad wants a string."""
    tree.standard()

    ipn, _ = editing.write_new_part(tree.root, form(fields={"Value": "100", "Fitted": "yes"}))

    assert load_parts(tree.root).parts[ipn].fields == {"Value": "100", "Fitted": "yes"}


def test_a_new_manufacturer_part_gets_a_file_and_a_known_one_is_left_alone(tree):
    tree.standard()
    known_before = (tree.root / "mpns/RC0603FR-0710KL.yaml").read_text(encoding="utf-8")
    sources = (
        SourceEntry("RC0603FR-0710KL", manufacturer="Someone else", lifecycle="obsolete"),
        SourceEntry(
            "ERJ-3EKF3300V/X", preferred=True, manufacturer="Panasonic",
            datasheet="https://example.com/erj.pdf", lifecycle="active",
        ),
    )  # fmt: skip

    ipn, _ = editing.write_new_part(tree.root, form(sources=sources))

    assert (tree.root / "mpns/RC0603FR-0710KL.yaml").read_text(encoding="utf-8") == known_before
    added = yaml.safe_load((tree.root / "mpns/ERJ-3EKF3300V_X.yaml").read_text(encoding="utf-8"))
    assert added == {
        "mpn": "ERJ-3EKF3300V/X",
        "manufacturer": "Panasonic",
        "datasheet": "https://example.com/erj.pdf",
        "lifecycle": "active",
    }
    index = load_parts(tree.root)
    assert index.issues == [], "a slash in a part number is not a file-name mismatch"
    assert index.parts[ipn].preferred_mpn == "ERJ-3EKF3300V/X"


def test_a_part_number_becomes_a_safe_file_name():
    assert mpn_file_stem("RC0603FR-0710KL") == "RC0603FR-0710KL"
    assert mpn_file_stem("LM358/NOPB") == "LM358_NOPB"
    assert mpn_file_stem("../../etc/passwd") == ".._.._etc_passwd", "and cannot leave mpns/"
    assert "/" not in mpn_file_stem("a/b\\c:d e") and "\\" not in mpn_file_stem("a/b\\c:d e")


def test_an_edit_keeps_the_number_the_status_and_what_it_does_not_know(tree):
    tree.standard()
    tree.write("parts/1102/1102-0001.yaml", PART + "notes: checked against the 2024 datasheet\n")

    message = editing.write_part(
        tree.root, "1102-0001", form(description="Resistor 10k, corrected")
    )

    data = yaml.safe_load((tree.root / "parts/1102/1102-0001.yaml").read_text(encoding="utf-8"))
    assert message == "Edit 1102-0001: Resistor 10k, corrected"
    assert (data["ipn"], data["status"]) == ("1102-0001", "approved")
    assert data["fields"] == {"Value": "330R"}
    assert data["notes"] == "checked against the 2024 datasheet", "carried along, not dropped"


def test_a_part_is_approved_and_retired(tree):
    tree.standard().part("1102-0002", PART.replace("approved", "draft"))

    assert editing.write_status(tree.root, "1102-0002", "approved").startswith("Approve 1102-0002")
    assert load_parts(tree.root).parts["1102-0002"].status == "approved"
    assert editing.write_status(tree.root, "1102-0002", "obsolete").startswith("Retire 1102-0002")

    with pytest.raises(EditError, match="already obsolete"):
        editing.write_status(tree.root, "1102-0002", "obsolete")
    with pytest.raises(EditError, match="There is no part 9999-0001"):
        editing.write_status(tree.root, "9999-0001", "approved")
    with pytest.raises(EditError, match="status must be one of"):
        editing.write_status(tree.root, "1102-0002", "finished")


# -- as commits -------------------------------------------------------------


class Repository:
    """The library's repository as the git host has it: bare, pushed to."""

    def __init__(self, tmp_path):
        seed = LibraryTree(tmp_path / "seed").standard()
        git("init", "-q", "-b", "main", cwd=seed.root)
        git("add", "-A", cwd=seed.root)
        git("-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "seed",
            cwd=seed.root)  # fmt: skip
        self.path = tmp_path / "origin.git"
        git("clone", "-q", "--bare", str(seed.root), str(self.path))
        self.source = Source("company", "Company Library", str(self.path))
        self._scratch = tmp_path / "someone-else"

    def show(self, path):
        return git("show", f"main:{path}", cwd=self.path)

    def last(self, fmt):
        return git("log", "-1", f"--format={fmt}", "main", cwd=self.path)

    def commits(self):
        return int(git("rev-list", "--count", "main", cwd=self.path))

    def push_from_elsewhere(self, relative, text):
        """Somebody else pushes a file, from their own clone."""
        if not self._scratch.exists():
            git("clone", "-q", str(self.path), str(self._scratch))
        git("pull", "-q", cwd=self._scratch)
        target = self._scratch / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git("add", "-A", cwd=self._scratch)
        git("-c", "user.name=Else", "-c", "user.email=else@example.com", "commit", "-q", "-m", "theirs",
            cwd=self._scratch)  # fmt: skip
        git("push", "-q", cwd=self._scratch)


@pytest.fixture()
def repository(tmp_path):
    return Repository(tmp_path)


@pytest.fixture()
def service(repository, tmp_path):
    return LibraryService(
        [repository.source], tmp_path / "work", publisher=Publisher(committer=APP)
    )


async def test_a_change_is_committed_in_the_person_s_name_and_pushed(service, repository):
    await service.refresh()

    snapshot = await service.change(lambda root: editing.write_new_part(root, form())[1], ADA)

    assert snapshot.part("1102-0002").description == "Resistor 330R 1% 0.1W 0603"
    assert service.snapshot is snapshot, "what is served is what was just written"
    assert "ipn: 1102-0002" in repository.show("parts/1102/1102-0002.yaml")
    # Who wanted it, and that it came through here.
    assert repository.last("%an <%ae>") == "Ada Lovelace <ada@example.com>"
    assert repository.last("%cn <%ce>") == "KiCad Library Manager <noreply@library.example.com>"
    assert repository.last("%s") == "Request 1102-0002: Resistor 330R 1% 0.1W 0603"


async def test_two_requests_at_once_get_different_numbers(service, repository):
    """Somebody pushes 1102-0002 between our fetch and our push. Ours is made
    again on top of theirs, and so takes the number after."""
    await service.refresh()
    attempts = []

    def write(root):
        if not attempts:
            repository.push_from_elsewhere(
                "parts/1102/1102-0002.yaml", PART.replace("1102-0001", "1102-0002")
            )
        ipn, message = editing.write_new_part(root, form())
        attempts.append(ipn)
        return message

    snapshot = await service.change(write, ADA)

    assert attempts == ["1102-0002", "1102-0003"]
    assert sorted(snapshot.parts.parts) == ["1102-0001", "1102-0002", "1102-0003"]
    assert snapshot.part("1102-0002").description == "Resistor 10k 1% 0.1W 0603", "theirs"
    assert snapshot.part("1102-0003").description == "Resistor 330R 1% 0.1W 0603", "ours"


async def test_a_change_that_is_refused_leaves_nothing_behind(service, repository, tmp_path):
    await service.refresh()
    before = repository.commits()

    def write(root):
        (root / "parts/1102/half-written.yaml").write_text("ipn: oops\n", encoding="utf-8")
        raise EditError(["No."])

    with pytest.raises(EditError):
        await service.change(write, ADA)

    assert repository.commits() == before
    assert not (tmp_path / "work/company/parts/1102/half-written.yaml").exists()
    assert (await service.refresh()).errors == [], "and the index is as it was"


async def test_a_change_that_changes_nothing_is_not_a_commit(service, repository):
    await service.refresh()
    before = repository.commits()

    with pytest.raises(PublishError, match="nothing was changed"):
        await service.change(lambda root: "An empty change", ADA)

    assert repository.commits() == before


async def test_without_a_publisher_the_library_is_read_only(repository, tmp_path):
    read_only = LibraryService([repository.source], tmp_path / "work")
    await read_only.refresh()

    assert read_only.writable is False
    with pytest.raises(PublishError, match="not set up"):
        await read_only.change(lambda root: "x", ADA)


def test_a_token_reaches_git_through_the_environment_only():
    """Not in the remote's address and not in an argument, where other
    processes could read it and git would quote it back in an error."""
    url = "https://github.com/acme/library.git"
    credentials = Publisher(committer=APP, token="s3cret-token")._credentials(url)

    assert credentials["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert credentials["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "s3cret-token" not in credentials["GIT_CONFIG_VALUE_0"], "and encoded, not bare"
    assert Publisher(committer=APP)._credentials(url) == {}


# -- through the web --------------------------------------------------------

REQUEST = {
    "category": "1102",
    "description": "Resistor 330R 1% 0.1W 0603",
    "symbol": "Passives:R_0603",
    "footprint": "Passives:R_0603_1608Metric",
    "fields": {"Value": "330R"},
    "sources": [{"mpn": "RC0603FR-0710KL", "preferred": True}],
}


@pytest.fixture()
def serve(repository, tmp_path, provider):
    """An app over the repository. Writable unless told otherwise."""
    clients = []

    def build(writable=True, **environment):
        library = LibraryService(
            [repository.source],
            tmp_path / "work",
            publisher=Publisher(committer=APP) if writable else None,
        )
        app = create_app(
            settings_for(**environment),
            library=library,
            identity=provider.client(),
            boms=BomStore(tmp_path / "boms.db"),
        )
        client = TestClient(app, base_url="https://library.example.com")
        client.__enter__()
        clients.append(client)
        wait_until_indexed(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture()
def web(serve):
    """Ada, a designer, signed in."""
    return sign_in(serve())


def test_a_designer_requests_a_part(web, repository):
    response = web.post("/api/v1/parts", json=REQUEST)

    assert response.status_code == 201
    assert response.json() == {"ipn": "1102-0002", "status": "draft"}
    # In the catalog at once, with no refresh and no restart.
    part = web.get("/api/v1/parts/1102-0002").json()
    assert (part["status"], part["fields"]) == ("draft", {"Value": "330R"})
    assert web.get("/api/v1/parts/1102-0002/assets").status_code == 200, "and can be placed"
    assert repository.last("%an|%s") == "Ada Lovelace|Request 1102-0002: Resistor 330R 1% 0.1W 0603"


def test_someone_with_no_e_mail_address_still_has_a_name_on_the_commit(web, repository):
    web.post("/api/v1/parts", json=REQUEST)
    assert repository.last("%ae") == "ada@users.noreply.library.example.com"


def test_a_request_that_makes_no_sense_lists_what_to_fix(web, repository):
    before = repository.commits()

    response = web.post(
        "/api/v1/parts", json={**REQUEST, "symbol": "Passives:Ghost", "description": ""}
    )

    assert response.status_code == 422
    assert response.json()["problems"] == [
        "Give the part a description; the number alone cannot be searched for.",
        "The library has no symbol Passives:Ghost.",
    ]
    assert repository.commits() == before


def test_a_draft_can_be_corrected_by_anyone(web, repository):
    web.post("/api/v1/parts", json=REQUEST)

    response = web.put("/api/v1/parts/1102-0002", json={**REQUEST, "description": "Resistor 330R"})

    assert response.status_code == 200
    assert web.get("/api/v1/parts/1102-0002").json()["description"] == "Resistor 330R"
    assert repository.last("%s") == "Edit 1102-0002: Resistor 330R"


def test_an_approved_part_is_the_librarians_to_change(web, repository):
    refused = web.put("/api/v1/parts/1102-0001", json=REQUEST)
    assert refused.status_code == 403
    assert web.get("/ipn/1102-0001/edit", headers={"Accept": "text/html"}).status_code == 403

    sign_in_librarian(web)
    assert web.put("/api/v1/parts/1102-0001", json=REQUEST).status_code == 200
    assert repository.last("%an") == "Grace Hopper"


def test_only_a_librarian_approves_and_retires(web, repository):
    web.post("/api/v1/parts", json=REQUEST)

    refused = web.post("/api/v1/parts/1102-0002/status", json={"status": "approved"})
    assert refused.status_code == 403
    assert web.get("/api/v1/parts/1102-0002").json()["status"] == "draft"

    sign_in_librarian(web)
    approved = web.post("/api/v1/parts/1102-0002/status", json={"status": "approved"})
    assert approved.json() == {"ipn": "1102-0002", "status": "approved"}
    assert repository.last("%an|%s") == "Grace Hopper|Approve 1102-0002: Resistor 330R 1% 0.1W 0603"

    assert (
        web.post("/api/v1/parts/1102-0002/status", json={"status": "obsolete"}).status_code == 200
    )
    assert web.get("/api/v1/parts/1102-0002").json()["status"] == "obsolete"
    again = web.post("/api/v1/parts/1102-0002/status", json={"status": "obsolete"})
    assert again.status_code == 422 and "already obsolete" in again.json()["detail"]
    assert web.post("/api/v1/parts/1102-0002/status", json={"status": "gone"}).status_code == 422


def test_the_form_is_offered_what_the_library_has(web):
    offered = web.get("/api/v1/library/choices").json()

    assert offered["writable"] is True
    assert {"code": "1102", "name": "Resistors, fixed"} in offered["categories"]
    assert offered["symbols"] == ["Passives:R", "Passives:R_0603"]
    assert offered["footprints"] == ["Passives:R_0603_1608Metric"]
    assert offered["mpns"] == [
        {
            "mpn": "RC0603FR-0710KL",
            "manufacturer": "Yageo",
            "lifecycle": "active",
            "used_by": ["1102-0001"],
        }
    ]


def test_the_pages_offer_what_the_reader_may_do(web):
    web.post("/api/v1/parts", json=REQUEST)

    draft, approved = web.get("/ipn/1102-0002").text, web.get("/ipn/1102-0001").text
    assert 'href="/ipn/1102-0002/edit"' in draft, "a draft is anyone's to correct"
    assert 'data-set-status="approved"' not in draft, "approving it is not"
    assert "/edit" not in approved and "data-set-status" not in approved
    assert 'href="/parts/new"' in web.get("/panel").text

    sign_in_librarian(web)
    assert 'data-set-status="approved"' in web.get("/ipn/1102-0002").text
    assert 'data-set-status="obsolete"' in web.get("/ipn/1102-0001").text
    assert 'href="/ipn/1102-0001/edit"' in web.get("/ipn/1102-0001").text


def test_the_form_pages_carry_the_part_as_data_not_markup(web):
    web.post("/api/v1/parts", json={**REQUEST, "description": 'R </script><script>alert("x")'})

    new, existing = web.get("/parts/new").text, web.get("/ipn/1102-0002/edit").text

    assert 'id="part-form"' in new and "part-form.js" in new and "Request a part" in new
    assert "Edit 1102-0002" in existing and '"ipn": "1102-0002"' in existing
    assert '<script>alert("x")' not in existing


def test_a_deployment_without_a_token_only_reads(serve):
    client = sign_in_librarian(serve(writable=False))

    assert client.post("/api/v1/parts", json=REQUEST).status_code == 503
    assert (
        client.post("/api/v1/parts/1102-0001/status", json={"status": "obsolete"}).status_code
        == 503
    )
    assert client.get("/api/v1/library/choices").json()["writable"] is False
    assert "cannot change the library" in client.get("/parts/new").text
    assert 'href="/parts/new"' not in client.get("/panel").text, "and does not offer to"
    assert "data-set-status" not in client.get("/ipn/1102-0001").text


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/parts"),
        ("PUT", "/api/v1/parts/1102-0001"),
        ("POST", "/api/v1/parts/1102-0001/status"),
        ("GET", "/api/v1/library/choices"),
        ("GET", "/parts/new"),
        ("GET", "/ipn/1102-0001/edit"),
    ],
)
def test_changing_the_library_needs_a_session(serve, method, path):
    assert serve().request(method, path, json=REQUEST).status_code == 401


def test_with_sign_in_off_changes_are_made_in_nobody_s_name(serve, repository):
    client = serve(**OPEN)

    assert client.post("/api/v1/parts", json=REQUEST).status_code == 201
    assert (
        client.post("/api/v1/parts/1102-0002/status", json={"status": "approved"}).status_code
        == 200
    )
    assert repository.last("%an <%ae>") == "Someone <nobody@users.noreply.library.example.com>"


# -- when the git host says no ----------------------------------------------


def refusal(output):
    from app.library.publish import _why_refused
    from app.library.sources import SourceError

    error = SourceError("git push failed: " + output.strip().splitlines()[-1])
    error.output = output
    return _why_refused(error)


def test_a_403_says_the_token_may_not_write():
    """The status alone sends people to check whether the token is right. It
    is: what it lacks is permission."""
    told = refusal(
        "remote: Permission to acme/library.git denied to ada.\n"
        'fatal: unable to access "https://github.com/acme/library.git/": '
        "The requested URL returned error: 403\n"
    )

    assert "Permission to acme/library.git denied to ada." in told, "what the host itself said"
    assert "recognised but may not push" in told
    assert "write access to the repository" in told


def test_a_401_says_the_token_was_not_accepted():
    told = refusal(
        "remote: Invalid username or password.\n"
        'fatal: Authentication failed for "https://github.com/acme/library.git/"\n'
    )

    assert "Invalid username or password." in told
    assert "mistyped, expired or revoked" in told


def test_any_other_failure_is_passed_on_as_git_put_it():
    assert refusal("fatal: unable to access: Could not resolve host: github.com\n") == (
        "git push failed: fatal: unable to access: Could not resolve host: github.com"
    )

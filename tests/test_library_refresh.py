"""Keeping the library current without a restart, and who may ask for it."""

import asyncio
import hashlib
import hmac
import time

import pytest

from app.auth.identity import Principal
from app.auth.roles import is_librarian
from app.library.service import LibraryService, State
from app.library.sources import Checkout, SourceError
from tests.conftest import OPEN, sign_in, sign_in_librarian
from tests.support import PART

# -- who is a librarian -----------------------------------------------------

LIBRARIANS = ("grace", "Librarian@Example.com", "subject-7")


@pytest.mark.parametrize(
    "principal,expected",
    [
        (Principal("u1", {"preferred_username": "grace"}), True),
        (Principal("u1", {"preferred_username": "GRACE"}), True),
        (Principal("u1", {"email": "librarian@example.com"}), True),
        (Principal("subject-7"), True),
        (Principal("u1", {"preferred_username": "ada"}), False),
        (Principal("u1", {"name": "grace"}), False),
        (None, False),
    ],
    ids=["username", "any-case", "email", "subject", "someone-else", "display-name", "nobody"],
)
def test_a_librarian_is_someone_the_settings_name(principal, expected):
    """By username, e-mail or subject -- not by display name, which is what a
    person chooses to be called and two people can choose alike."""
    assert is_librarian(principal, LIBRARIANS, auth_configured=True) is expected


def test_with_sign_in_off_everyone_is_a_librarian():
    """There is nobody to tell apart, on a network declared to be trusted."""
    assert is_librarian(None, (), auth_configured=False) is True
    assert is_librarian(None, (), auth_configured=True) is False


# -- the service ------------------------------------------------------------


class MovingSync:
    """A source whose commit moves when the test says so."""

    def __init__(self, tree):
        self.tree = tree
        self.commit = "commit-1"
        self.calls = 0
        self.error = None

    def __call__(self, source, workdir) -> Checkout:
        self.calls += 1
        if self.error:
            raise SourceError(self.error)
        return Checkout(source=source, path=self.tree.root, commit=self.commit)

    def push(self, commit):
        self.commit = commit


@pytest.fixture()
def moving(tree):
    return MovingSync(tree.standard())


async def test_a_source_that_has_not_moved_is_not_indexed_again(moving, tree, tmp_path):
    """Looking is cheap, so it can be done often: only a new commit costs
    a read of every file."""
    service = LibraryService([tree.source], tmp_path, sync=moving)
    first = await service.refresh()

    again = await service.refresh()

    assert again is first, "the very same snapshot, not an equal one"
    assert moving.calls == 2, "it did look"
    assert service.checked_at >= first.indexed_at


async def test_a_new_commit_is_indexed(moving, tree, tmp_path):
    service = LibraryService([tree.source], tmp_path, sync=moving)
    first = await service.refresh()

    tree.part("1102-0002")
    moving.push("commit-2")
    second = await service.refresh()

    assert second is not first
    assert (first.part_count, second.part_count) == (1, 2)
    assert second.sources[0].commit == "commit-2"


async def test_a_source_that_came_back_clears_the_error(moving, tree, tmp_path):
    service = LibraryService([tree.source], tmp_path, sync=moving)
    await service.refresh()

    moving.error = "git fetch failed"
    await service.refresh()
    assert service.error and service.state is State.READY

    moving.error = None
    await service.refresh()
    assert service.error is None


async def test_the_library_is_looked_at_on_a_timer(moving, tree, tmp_path):
    service = LibraryService([tree.source], tmp_path, sync=moving, refresh_interval_seconds=0.02)
    service.start()
    try:
        deadline = time.monotonic() + 5
        while moving.calls < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert moving.calls >= 3, "the first index, and then again and again"

        tree.part("1102-0002")
        moving.push("commit-2")
        while service.snapshot.part_count < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert service.snapshot.part_count == 2, "and what was pushed arrives by itself"
    finally:
        await service.stop()


async def test_without_an_interval_nothing_looks_again(moving, tree, tmp_path):
    service = LibraryService([tree.source], tmp_path, sync=moving)
    service.start()
    await service._task

    await asyncio.sleep(0.05)

    assert moving.calls == 1
    await service.stop()


async def test_requests_that_arrive_together_are_one_refresh(moving, tree, tmp_path):
    """A push of five commits is one change to the library, not five."""
    service = LibraryService([tree.source], tmp_path, sync=moving)

    assert service.request_refresh() is True
    assert service.request_refresh() is True
    assert service.request_refresh() is True
    await service._requested

    assert moving.calls == 1
    assert service.state is State.READY
    assert LibraryService([], tmp_path).request_refresh() is False, "nothing to refresh"
    await service.stop()


# -- the button -------------------------------------------------------------


@pytest.fixture()
def served(make_client, tree):
    """A library being served, whose repository the test can then change."""
    return make_client(tree.standard())


def test_a_librarian_can_refresh_the_library(served, tree):
    sign_in_librarian(served)
    assert served.get("/api/v1/parts").json()["total"] == 1

    # Somebody pushes a part. The fixture's sync always reports one commit, so
    # the source is made to look as though it moved.
    tree.part("1102-0002")
    served.app.state.library._sync = lambda source, workdir: Checkout(source, tree.root, "newer")
    outcome = served.post("/api/v1/library/refresh").json()

    assert outcome["changed"] is True
    assert (outcome["state"], outcome["parts"], outcome["error"]) == ("ready", 2, None)
    assert outcome["sources"] == [{"id": "company", "commit": "newer", "error": None}]
    assert outcome["checked_at"] > 0
    assert served.get("/api/v1/parts").json()["total"] == 2, "no restart needed"


def test_a_refresh_that_finds_nothing_new_says_so(served):
    outcome = sign_in_librarian(served).post("/api/v1/library/refresh").json()
    assert (outcome["changed"], outcome["parts"]) == (False, 1)


def test_a_refresh_that_cannot_reach_a_source_says_so_and_keeps_serving(served):
    def unreachable(source, workdir):
        raise SourceError("git fetch failed: could not resolve host")

    sign_in_librarian(served)
    served.app.state.library._sync = unreachable

    outcome = served.post("/api/v1/library/refresh").json()

    assert outcome["error"] == "company: git fetch failed: could not resolve host"
    assert (outcome["state"], outcome["parts"], outcome["changed"]) == ("ready", 1, False)


def test_only_a_librarian_can_refresh(served):
    assert served.post("/api/v1/library/refresh").status_code == 401, "sign in first"

    refused = sign_in(served).post("/api/v1/library/refresh")

    assert refused.status_code == 403
    assert refused.json() == {"detail": "Only a librarian can do this."}


def test_the_status_page_offers_the_refresh_to_librarians_only(served):
    assert 'id="refresh-now"' not in served.get("/").text
    assert 'id="refresh-now"' not in sign_in(served).get("/").text

    page = sign_in_librarian(served).get("/").text

    assert 'id="refresh-now"' in page and "status.js" in page
    assert "Checked every 15 minutes." in page
    assert "Last checked 20" in page


def test_the_status_page_says_how_the_library_is_kept_current(make_client, tree):
    def page(**environment):
        return make_client(tree.standard(), **environment).get("/").text

    assert "and when the git host reports a push" in page(WEBHOOK_SECRET="s3cret")
    assert "Checked when the git host reports a push." in page(
        WEBHOOK_SECRET="s3cret", LIBRARY_REFRESH_MINUTES="0"
    )
    assert "Not checked again until the app restarts" in page(LIBRARY_REFRESH_MINUTES="0")


def test_with_sign_in_off_anyone_can_refresh(make_client, tree):
    client = make_client(tree.standard(), **OPEN)

    assert client.post("/api/v1/library/refresh").status_code == 200
    assert 'id="refresh-now"' in client.get("/").text


# -- the webhook ------------------------------------------------------------

SECRET = "a-long-shared-secret"
BODY = b'{"ref":"refs/heads/main","commits":[{"id":"abc"}]}'


def signature(body=BODY, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def hooked(make_client, tree):
    """Nobody signed in: the git host has no session."""
    return make_client(tree.standard(), WEBHOOK_SECRET=SECRET)


def push_event(client, **headers):
    return client.post("/api/v1/library/webhook", content=BODY, headers=headers)


def wait_for(condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Hub-Signature-256": f"sha256={signature()}"},
        {"X-Gitea-Signature": signature()},
        {"X-Gitea-Signature": signature(), "X-Hub-Signature-256": f"sha256={signature()}"},
    ],
    ids=["github", "gitea", "both"],
)
def test_a_signed_push_refreshes_the_library(hooked, tree, headers):
    tree.part("1102-0002")
    hooked.app.state.library._sync = lambda source, workdir: Checkout(source, tree.root, "newer")

    response = push_event(hooked, **headers)

    assert response.status_code == 202, "answered at once; the fetch is not waited for"
    assert wait_for(lambda: hooked.app.state.library.snapshot.part_count == 2)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Hub-Signature-256": "sha256=" + "0" * 64},
        {"X-Hub-Signature-256": f"sha256={signature(secret='another-secret')}"},
        {"X-Hub-Signature-256": f"sha256={signature(body=b'{}')}"},
        {"X-Gitea-Signature": ""},
        {"Authorization": f"Bearer {SECRET}"},
    ],
    ids=["unsigned", "zeros", "wrong-secret", "other-body", "empty", "secret-as-token"],
)
def test_a_push_that_is_not_signed_with_the_secret_is_refused(hooked, headers):
    calls = []
    hooked.app.state.library._sync = lambda source, workdir: calls.append(1)

    assert push_event(hooked, **headers).status_code == 401
    assert calls == []


def test_there_is_no_webhook_without_a_secret(make_client, tree):
    client = make_client(tree.standard())
    response = push_event(client, **{"X-Hub-Signature-256": f"sha256={signature(secret='')}"})

    assert response.status_code == 404


def test_the_first_part_of_a_new_part_s_life(served, tree):
    """What all of this is for: a part is pushed, and without anyone
    restarting anything it can be found and placed."""
    sign_in_librarian(served)
    tree.part("1102-0002", PART.replace("10k", "4k7"))
    served.app.state.library._sync = lambda source, workdir: Checkout(source, tree.root, "newer")

    served.post("/api/v1/library/refresh")

    found = served.get("/api/v1/parts", params={"q": "4k7"}).json()["parts"]
    assert [part["ipn"] for part in found] == ["1102-0002"]
    assert served.get("/api/v1/parts/1102-0002/assets").status_code == 200

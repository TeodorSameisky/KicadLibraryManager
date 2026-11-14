"""Keeping checkouts current, and a snapshot available.

The git tests use real repositories on disk rather than a stand-in for git:
what they are about is exactly what git does with a clone.
"""

import shutil
import subprocess
import threading

import pytest

from app.library.service import LibraryService, State
from app.library.sources import Checkout, Source, SourceError, parse_sources, sync
from tests.support import LibraryTree

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def commit(repository, message="change"):
    git("add", "-A", cwd=repository)
    git(
        "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message,
        cwd=repository,
    )  # fmt: skip


@pytest.fixture()
def origin(tmp_path):
    """A git repository holding the standard library."""
    tree = LibraryTree(tmp_path / "origin").standard()
    git("init", "-b", "main", cwd=tree.root)
    commit(tree.root, "initial")
    return tree


@pytest.fixture()
def source(origin):
    return Source(id="company", name="Company", url=str(origin.root))


# -- configuration ----------------------------------------------------------


def test_sources_are_a_json_array():
    sources = parse_sources(
        '[{"id":"company","name":"Company Library","url":"https://example.com/a.git","ref":"v2"},'
        ' {"id":"kicad","url":"https://example.com/b.git"}]'
    )

    assert sources[0] == Source("company", "Company Library", "https://example.com/a.git", "v2")
    assert sources[1].name == "kicad", "the id, when no name is given"
    assert sources[1].ref == "main"


@pytest.mark.parametrize("text", [None, "", "   ", "[]"])
def test_no_sources_is_not_an_error(text):
    assert parse_sources(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '["a string"]',
        '[{"url":"x"}]',
        '[{"id":"a"}]',
        '[{"id":"a/b","url":"x"}]',
        '[{"id":"..","url":"x"}]',
        '[{"id":"a","url":"x"},{"id":"a","url":"y"}]',
    ],
    ids=["not-json", "not-a-list", "not-an-object", "no-id", "no-url", "slash", "dots", "twice"],
)
def test_a_source_list_that_cannot_be_used_is_refused(text):
    """The id names the directory the repository is cloned into."""
    with pytest.raises(SourceError):
        parse_sources(text)


# -- git --------------------------------------------------------------------


@needs_git
def test_a_source_is_cloned(source, tmp_path):
    checkout = sync(source, tmp_path / "work")

    assert checkout.path == tmp_path / "work" / "company"
    assert (checkout.path / "categories.yaml").is_file()
    assert len(checkout.commit) == 40


@needs_git
def test_a_clone_follows_its_origin(source, origin, tmp_path):
    first = sync(source, tmp_path / "work")

    origin.part("1102-0002")
    (origin.root / "categories.yaml").unlink()
    commit(origin.root)
    second = sync(source, tmp_path / "work")

    assert second.commit != first.commit
    assert (second.path / "parts/1102/1102-0002.yaml").is_file()
    assert not (second.path / "categories.yaml").exists(), "deletions arrive too"


@needs_git
def test_a_directory_that_is_not_a_clone_is_replaced(source, tmp_path):
    """An interrupted clone must not leave the platform broken until someone
    deletes the directory by hand."""
    leftover = tmp_path / "work" / "company"
    leftover.mkdir(parents=True)
    (leftover / "junk.txt").write_text("from a clone that never finished")

    checkout = sync(source, tmp_path / "work")

    assert (checkout.path / "categories.yaml").is_file()
    assert not (checkout.path / "junk.txt").exists()


@needs_git
def test_a_changed_address_repoints_the_clone(source, origin, tmp_path):
    """Otherwise a fetch quietly keeps pulling from the old repository."""
    sync(source, tmp_path / "work")
    moved = tmp_path / "moved"
    shutil.move(origin.root, moved)

    checkout = sync(Source("company", "Company", str(moved)), tmp_path / "work")

    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=checkout.path, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert remote == str(moved)


@needs_git
def test_an_unreachable_origin_leaves_the_clone_where_it_was(source, tmp_path):
    """The current index points at these files. Losing the git host must not
    take them away."""
    sync(source, tmp_path / "work")
    gone = Source("company", "Company", str(tmp_path / "nowhere"))

    with pytest.raises(SourceError, match="git clone failed"):
        sync(gone, tmp_path / "work")

    assert (tmp_path / "work" / "company" / "categories.yaml").is_file()
    assert [p.name for p in (tmp_path / "work").iterdir()] == ["company"], "nothing left behind"


# -- the service ------------------------------------------------------------


@needs_git
async def test_a_repository_is_cloned_and_indexed(source, tmp_path):
    service = LibraryService([source], tmp_path / "work")
    assert service.state is State.SYNCING

    snapshot = await service.refresh()

    assert service.state is State.READY
    assert service.snapshot is snapshot
    assert snapshot.part_count == 1
    assert snapshot.catalog.symbol_count == 2
    assert snapshot.errors == []
    assert snapshot.indexed_at is not None
    assert snapshot.sources[0].commit


@needs_git
async def test_a_refresh_picks_up_new_commits(source, origin, tmp_path):
    service = LibraryService([source], tmp_path / "work")
    before = await service.refresh()

    origin.part("1102-0002")
    commit(origin.root)
    after = await service.refresh()

    assert after.sources[0].commit != before.sources[0].commit
    assert after.part_count == 2
    assert before.part_count == 1, "a snapshot already handed out does not change"


class FlakySync:
    """A sync that can be told to fail, in place of git."""

    def __init__(self, tree):
        self.tree = tree
        self.error = None

    def __call__(self, source, workdir) -> Checkout:
        if self.error:
            raise SourceError(self.error)
        return self.tree.checkout


async def test_a_failed_refresh_keeps_the_previous_index(tree, tmp_path):
    """Losing the git host must not empty a catalog people are using."""
    flaky = FlakySync(tree.standard())
    service = LibraryService([tree.source], tmp_path, sync=flaky)
    good = await service.refresh()

    flaky.error = "git fetch failed: could not resolve host"
    served = await service.refresh()

    assert served is good and service.snapshot is good
    assert service.state is State.READY
    assert service.error == "company: git fetch failed: could not resolve host"

    flaky.error = None
    await service.refresh()
    assert service.error is None, "and it clears once the source is back"


async def test_a_first_index_that_fails_is_an_error_state(tree, tmp_path):
    flaky = FlakySync(tree.standard())
    flaky.error = "git clone failed: repository not found"
    service = LibraryService([tree.source], tmp_path, sync=flaky)

    await service.refresh()

    assert service.state is State.ERROR
    assert service.snapshot.part_count == 0
    assert service.snapshot.sources[0].error == "git clone failed: repository not found"

    flaky.error = None
    await service.refresh()
    assert service.state is State.READY, "and it recovers"


async def test_with_no_sources_there_is_nothing_to_index(tmp_path):
    service = LibraryService([], tmp_path)

    await service.refresh()
    service.start()

    assert service.state is State.EMPTY
    assert service.snapshot.part_count == 0


async def test_the_first_index_runs_in_the_background(tree, tmp_path):
    """Cloning takes longer than a container is given to become healthy, so
    starting must not wait for it."""
    release = threading.Event()
    tree.standard()

    def slow_sync(source, workdir):
        release.wait(timeout=10)
        return tree.checkout

    service = LibraryService([tree.source], tmp_path, sync=slow_sync)
    service.start()

    assert service.state is State.SYNCING
    assert service.snapshot.part_count == 0, "and it can be asked things meanwhile"

    release.set()
    await service.refresh()  # queues behind the one already running
    assert service.state is State.READY
    await service.stop()


async def test_a_crash_while_indexing_does_not_take_the_app_down(tree, tmp_path):
    def broken_sync(source, workdir):
        raise RuntimeError("something nobody anticipated")

    service = LibraryService([tree.source], tmp_path, sync=broken_sync)
    service.start()
    await service._task

    assert service.state is State.ERROR
    assert "something nobody anticipated" not in service.error, "details stay in the log"
    await service.stop()

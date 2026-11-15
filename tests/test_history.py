"""A part's history, read from the repository it is kept in."""

import shutil
import subprocess

import pytest

from app.library.history import (
    _UNREADABLE,
    HistoryUnavailable,
    commit_url,
    describe,
    read_history,
)
from app.library.sources import Source, sync
from tests.conftest import sign_in_librarian

# The fixtures that build a repository and an app over it.
from tests.test_editing import REQUEST, repository, serve, web  # noqa: F401

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


# -- describing a change ----------------------------------------------------

BEFORE = {
    "ipn": "1102-0001",
    "description": "Resistor 10k",
    "status": "draft",
    "symbol": "Passives:R_0603",
    "footprint": "Passives:R_0603_1608Metric",
    "fields": {"Value": "10k", "Tolerance": "1%", "Power": "0.1W"},
    "mpns": [{"mpn": "A", "preferred": True}, "B"],
}


def changed(**changes):
    return {**BEFORE, **changes}


def test_a_new_part_says_how_it_started():
    assert describe(None, BEFORE) == ("Created as draft.", "Description: Resistor 10k")
    assert describe(None, {"ipn": "x"}) == ("Created as draft.",)


def test_a_deleted_part_says_so():
    assert describe(BEFORE, None) == ("Deleted.",)
    assert describe(None, None) == ()


def test_nothing_changed_says_nothing():
    """A reformatted file is a commit, and not a change to the part."""
    assert describe(BEFORE, dict(BEFORE)) == ()


def test_each_kind_of_change_is_put_into_words():
    after = changed(
        status="approved",
        description="Resistor 10k 1%",
        footprint="",
        fields={"Value": "10k", "Tolerance": "5%", "Voltage": "50V"},
        mpns=[{"mpn": "B", "preferred": True}, "C"],
    )

    assert describe(BEFORE, after) == (
        'Status: "draft" → "approved"',
        'Description: "Resistor 10k" → "Resistor 10k 1%"',
        'Footprint: "Passives:R_0603_1608Metric" → empty',
        "Removed Power (it was 0.1W)",  # fields, by name
        'Tolerance: "1%" → "5%"',
        "Added Voltage: 50V",
        "Removed source A",  # sources, by number: what was added or removed first,
        "Added source C",
        "B is now the preferred source",  # then who is preferred
    )


def test_a_source_that_stops_being_preferred_is_said():
    after = changed(mpns=["A", "B"])
    assert describe(BEFORE, after) == ("A is no longer the preferred source",)


def test_what_a_hand_written_file_carries_beyond_this_is_said_once():
    assert describe(BEFORE, changed(notes="checked against the datasheet")) == ("notes changed",)


def test_a_version_that_cannot_be_read_is_said_and_not_guessed_at():
    assert describe(_UNREADABLE, BEFORE) == ("The file could not be read as a part at this point.",)


def test_the_same_changes_always_come_out_in_the_same_order():
    after = changed(fields={"Z": "1", "A": "2", "Value": "10k", "Tolerance": "1%", "Power": "0.1W"})
    assert describe(BEFORE, after) == describe(BEFORE, after)
    assert describe(BEFORE, after) == ("Added A: 2", "Added Z: 1")


# -- a link to the commit on the git host -----------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/Acme/library.git", "https://github.com/Acme/library/commit/abc"),
        ("https://github.com/Acme/library", "https://github.com/Acme/library/commit/abc"),
        (
            "https://gitea.example.com:3000/acme/lib.git/",
            "https://gitea.example.com:3000/acme/lib/commit/abc",
        ),
        ("git@github.com:Acme/library.git", "https://github.com/Acme/library/commit/abc"),
        ("/some/local/path", None),
        ("http://insecure.example.com/a/b.git", None),
    ],
)
def test_a_commit_is_linked_where_the_host_shows_it(url, expected):
    assert commit_url(url, "abc") == expected


def test_a_token_in_a_configured_address_never_reaches_a_page():
    """It is in front of the host, and the link is built from the host on."""
    link = commit_url("https://x-access-token:ghp_SECRET@github.com/Acme/library.git", "abc")

    assert link == "https://github.com/Acme/library/commit/abc"
    assert "SECRET" not in link


# -- reading a repository ---------------------------------------------------


def git(*args, cwd):
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(repo, message, author="Ada Lovelace <ada@example.com>"):
    git("add", "-A", cwd=repo)
    git(
        "-c", "user.name=App", "-c", "user.email=app@example.com",
        "commit", "-q", "--author", author, "-m", message, cwd=repo,
    )  # fmt: skip


@pytest.fixture()
def repo(tmp_path):
    path = tmp_path / "repo"
    (path / "parts/1102").mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    return path


def write(repo, text, name="1102-0001"):
    (repo / "parts/1102" / f"{name}.yaml").write_text(text, encoding="utf-8")


@needs_git
def test_a_files_changes_are_listed_newest_first(repo):
    write(repo, "ipn: 1102-0001\ndescription: Resistor\nstatus: draft\n")
    commit(repo, "Request 1102-0001: Resistor", "Ada Lovelace <ada@example.com>")
    write(repo, "ipn: 1102-0001\ndescription: Resistor\nstatus: approved\n")
    commit(repo, "Approve 1102-0001: Resistor", "Grace Hopper <grace@example.com>")
    write(repo, "ipn: 1102-0002\ndescription: Another\nstatus: draft\n", name="1102-0002")
    commit(repo, "Request 1102-0002: Another")  # not this part's business

    history = read_history(repo, "parts/1102/1102-0001.yaml")

    assert history.more is False
    assert [(c.author, c.subject) for c in history.changes] == [
        ("Grace Hopper", "Approve 1102-0001: Resistor"),
        ("Ada Lovelace", "Request 1102-0001: Resistor"),
    ]
    assert history.changes[0].what == ('Status: "draft" → "approved"',)
    assert history.changes[1].what == ("Created as draft.", "Description: Resistor")
    assert all(len(c.commit) == 40 and c.when > 0 for c in history.changes)


@needs_git
def test_a_deleted_file_has_its_last_word(repo):
    write(repo, "ipn: 1102-0001\ndescription: Resistor\n")
    commit(repo, "add")
    (repo / "parts/1102/1102-0001.yaml").unlink()
    commit(repo, "remove")

    history = read_history(repo, "parts/1102/1102-0001.yaml")

    assert [c.what for c in history.changes] == [("Deleted.",), history.changes[1].what]
    assert history.changes[1].what[0] == "Created as draft."


@needs_git
def test_a_long_history_stops_where_it_is_told_to(repo):
    for number in range(5):
        write(repo, f"ipn: 1102-0001\ndescription: Version {number}\n")
        commit(repo, f"Edit {number}")

    history = read_history(repo, "parts/1102/1102-0001.yaml", limit=3)

    assert [c.subject for c in history.changes] == ["Edit 4", "Edit 3", "Edit 2"]
    assert history.more is True


@needs_git
def test_a_version_that_is_not_yaml_does_not_stop_the_rest(repo):
    write(repo, "ipn: 1102-0001\ndescription: Resistor\n")
    commit(repo, "good")
    write(repo, "ipn: [unclosed\n")
    commit(repo, "broken")
    write(repo, "ipn: 1102-0001\ndescription: Resistor again\n")
    commit(repo, "fixed")

    broken, fixed = (c.what for c in read_history(repo, "parts/1102/1102-0001.yaml").changes[:2])

    assert broken[0].startswith("The file could not be read")
    assert fixed[0].startswith("The file could not be read")


@needs_git
def test_a_directory_that_is_not_a_repository_has_no_history(tmp_path):
    with pytest.raises(HistoryUnavailable, match="not a git repository"):
        read_history(tmp_path, "parts/1102/1102-0001.yaml")


@needs_git
def test_a_shallow_clone_has_no_history_and_does_not_pretend_to(repo, tmp_path):
    """A single commit would read as every part having been created at once."""
    write(repo, "ipn: 1102-0001\ndescription: Resistor\n")
    commit(repo, "one")
    write(repo, "ipn: 1102-0001\ndescription: Resistor 2\n")
    commit(repo, "two")
    shallow = tmp_path / "shallow"
    git("clone", "-q", "--depth", "1", f"file://{repo.as_posix()}", str(shallow), cwd=tmp_path)

    with pytest.raises(HistoryUnavailable, match="without its history"):
        read_history(shallow, "parts/1102/1102-0001.yaml")


# -- clones keep their history ----------------------------------------------


@needs_git
def test_a_clone_has_the_history_and_a_shallow_one_is_replaced(repo, tmp_path):
    write(repo, "ipn: 1102-0001\ndescription: Resistor\n")
    commit(repo, "one")
    write(repo, "ipn: 1102-0001\ndescription: Resistor 2\n")
    commit(repo, "two")
    source = Source("company", "Company", repo.as_posix())

    # A clone made before history was kept.
    old = tmp_path / "work" / "company"
    old.parent.mkdir()
    git("clone", "-q", "--depth", "1", f"file://{repo.as_posix()}", str(old), cwd=tmp_path)
    assert git("rev-parse", "--is-shallow-repository", cwd=old) == "true"

    checkout = sync(source, tmp_path / "work")

    assert git("rev-parse", "--is-shallow-repository", cwd=checkout.path) == "false"
    assert git("rev-list", "--count", "HEAD", cwd=checkout.path) == "2"
    assert (checkout.path / "parts/1102/1102-0001.yaml").is_file()


# -- through the web --------------------------------------------------------


@needs_git
def test_a_part_s_history_is_what_was_done_to_it_through_the_app(serve):  # noqa: F811
    """Requested by a designer, corrected, approved by a librarian: the
    repository has three commits in three names, and so does the page."""
    designer = serve()
    from tests.conftest import sign_in

    sign_in(designer)
    designer.post("/api/v1/parts", json=REQUEST)
    designer.put("/api/v1/parts/1102-0002", json={**REQUEST, "description": "Resistor 330R, 1%"})
    sign_in_librarian(designer)
    designer.post("/api/v1/parts/1102-0002/status", json={"status": "approved"})

    body = designer.get("/api/v1/parts/1102-0002/history").json()

    assert body["more"] is False
    assert [(c["author"], c["what"][0]) for c in body["changes"]] == [
        ("Grace Hopper", 'Status: "draft" → "approved"'),
        ("Ada Lovelace", 'Description: "Resistor 330R 1% 0.1W 0603" → "Resistor 330R, 1%"'),
        ("Ada Lovelace", "Created as draft."),
    ]
    assert body["changes"][0]["subject"].startswith("Approve 1102-0002")
    assert all(c["url"] is None for c in body["changes"]), "a local repository has no web page"

    page = designer.get("/ipn/1102-0002/history").text
    assert "History of 1102-0002" in page
    assert "Grace Hopper" in page and "Ada Lovelace" in page
    assert "Status: &#34;draft&#34; → &#34;approved&#34;" in page or "→" in page
    assert 'href="/ipn/1102-0002"' in page


@needs_git
def test_a_part_that_has_not_been_changed_has_its_creation(web):  # noqa: F811
    changes = web.get("/api/v1/parts/1102-0001/history").json()["changes"]

    assert [c["subject"] for c in changes] == ["seed"]
    assert changes[0]["what"][0] == "Created as approved."


def test_the_part_page_links_to_its_history(web):  # noqa: F811
    assert 'href="/ipn/1102-0001/history"' in web.get("/ipn/1102-0001").text


def test_a_part_that_does_not_exist_has_no_history(web):  # noqa: F811
    assert web.get("/api/v1/parts/9999-9999/history").status_code == 404
    assert web.get("/ipn/9999-9999/history").status_code == 404


def test_history_is_closed_to_someone_signed_out(serve):  # noqa: F811
    client = serve()
    assert client.get("/api/v1/parts/1102-0001/history").status_code == 401
    assert client.get("/ipn/1102-0001/history").status_code == 401


def test_a_library_without_a_repository_says_so_instead_of_failing(library_client):
    """The fixture's checkout is a plain directory, not a git repository."""
    api = library_client.get("/api/v1/parts/1102-0001/history")
    page = library_client.get("/ipn/1102-0001/history")

    assert api.status_code == 503
    assert api.json() == {
        "detail": "No history: the library is not a git repository on this server."
    }
    assert page.status_code == 200
    assert "The history cannot be shown right now" in page.text
    assert str(library_client.app.state.library._checkouts) not in page.text, "no server paths"

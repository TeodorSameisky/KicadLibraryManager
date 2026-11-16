"""Reading a private repository: a token sent to one source's host, and nowhere else."""

import pytest

from app.library import sources as sources_module
from app.library.history import HistoryUnavailable
from app.library.service import LibraryService
from app.library.snapshot import Snapshot, SourceStatus
from app.library.sources import Source, SourceError, credentials, parse_sources, sync
from tests.conftest import settings_for

TOKEN = "ghp_s3cret"
PRIVATE = Source("company", "Company", "https://github.com/acme/library.git", private=True)
PUBLIC = Source("kicad", "KiCad", "https://gitlab.com/kicad/libraries.git")


# -- reading the setting ----------------------------------------------------


def test_a_source_is_public_unless_it_says_it_is_private():
    sources = parse_sources(
        '[{"id":"a","url":"https://x/a.git","private":true}, {"id":"b","url":"https://x/b.git"}]'
    )
    assert [s.private for s in sources] == [True, False]


# -- the credentials --------------------------------------------------------


def test_a_token_is_scoped_to_the_host_it_is_for():
    """If the repository redirects somewhere else, it does not follow."""
    env = credentials("https://github.com/acme/library.git", TOKEN)

    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")


def test_a_port_is_part_of_the_scope():
    env = credentials("https://gitea.example.com:3000/acme/lib.git", TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://gitea.example.com:3000/.extraHeader"


def test_the_token_is_encoded_and_never_in_the_clear():
    env = credentials("https://github.com/acme/library.git", TOKEN)
    assert TOKEN not in "".join(env.values())


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/library.git",  # it would cross the network in the clear
        "git@github.com:acme/library.git",  # ssh has keys of its own
        "/some/local/path",
        "file:///some/local/path",
    ],
)
def test_a_token_is_only_ever_sent_over_https(url):
    assert credentials(url, TOKEN) == {}


def test_no_token_means_no_credentials():
    assert credentials("https://github.com/acme/library.git", None) == {}
    assert credentials("https://github.com/acme/library.git", "") == {}


# -- what sync sends --------------------------------------------------------


class FakeGit:
    """Stands in for git, remembering how it was called."""

    def __init__(self, fail_clone_with=None):
        self.calls = []
        self.fail_clone_with = fail_clone_with

    def __call__(self, *args, cwd=None, env=None):
        self.calls.append((args, dict(env or {})))
        if args[0] == "clone":
            if self.fail_clone_with is not None:
                raise self.fail_clone_with
            from pathlib import Path

            (Path(args[-1]) / ".git").mkdir(parents=True)
        if args[:2] == ("rev-parse", "--is-shallow-repository"):
            return "false"
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        return ""

    def env_of(self, command):
        return next(env for args, env in self.calls if args[0] == command)


@pytest.fixture()
def fake_git(monkeypatch):
    fake = FakeGit()
    monkeypatch.setattr(sources_module, "_git", fake)
    return fake


def test_a_private_source_is_cloned_with_the_token(fake_git, tmp_path):
    sync(PRIVATE, tmp_path, token=TOKEN)

    assert fake_git.env_of("clone")["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"


def test_a_private_source_is_fetched_with_the_token(fake_git, tmp_path):
    sync(PRIVATE, tmp_path, token=TOKEN)  # the clone
    fake_git.calls.clear()

    sync(PRIVATE, tmp_path, token=TOKEN)  # an update

    for command in ("fetch", "reset"):
        assert "GIT_CONFIG_KEY_0" in fake_git.env_of(command), command


def test_a_public_source_is_never_sent_the_token(fake_git, tmp_path):
    """A token for one host must not go to another: the deployment may read
    the official libraries from a host that is not the one the token is for."""
    sync(PUBLIC, tmp_path, token=TOKEN)
    sync(PUBLIC, tmp_path, token=TOKEN)

    assert all(env == {} for _, env in fake_git.calls)


def test_a_private_source_with_no_token_sends_nothing(fake_git, tmp_path):
    sync(PRIVATE, tmp_path)
    assert all(env == {} for _, env in fake_git.calls)


# -- when it does not work --------------------------------------------------


def refused(output):
    error = SourceError("git clone failed: " + output.strip().splitlines()[-1])
    error.output = output
    return error


PROMPT = "fatal: could not read Username for 'https://github.com': terminal prompts disabled\n"


def test_a_public_source_that_wants_a_login_says_it_may_be_private(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_module, "_git", FakeGit(fail_clone_with=refused(PROMPT)))

    with pytest.raises(SourceError) as failed:
        sync(PUBLIC, tmp_path)

    assert '"private": true' in str(failed.value) and "LIBRARY_READ_TOKEN" in str(failed.value)


def test_a_private_source_with_no_token_says_a_token_is_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_module, "_git", FakeGit(fail_clone_with=refused(PROMPT)))

    with pytest.raises(SourceError, match="neither LIBRARY_READ_TOKEN nor LIBRARY_WRITE_TOKEN"):
        sync(PRIVATE, tmp_path)


def test_a_token_that_is_refused_says_so(monkeypatch, tmp_path):
    notfound = refused(
        "remote: Repository not found.\n"
        "fatal: repository 'https://github.com/acme/library.git/' not found\n"
    )
    monkeypatch.setattr(sources_module, "_git", FakeGit(fail_clone_with=notfound))

    with pytest.raises(SourceError, match="token was not accepted, or may not read"):
        sync(PRIVATE, tmp_path, token=TOKEN)


def test_any_other_failure_is_passed_on_as_git_put_it(monkeypatch, tmp_path):
    other = refused("fatal: unable to access: Could not resolve host: github.com\n")
    monkeypatch.setattr(sources_module, "_git", FakeGit(fail_clone_with=other))

    with pytest.raises(SourceError) as failed:
        sync(PRIVATE, tmp_path, token=TOKEN)

    assert (
        str(failed.value)
        == "git clone failed: fatal: unable to access: Could not resolve host: github.com"
    )


def test_the_token_is_not_in_any_message_git_gave(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_module, "_git", FakeGit(fail_clone_with=refused(PROMPT)))

    with pytest.raises(SourceError) as failed:
        sync(PRIVATE, tmp_path, token=TOKEN)

    assert TOKEN not in str(failed.value)


# -- the settings -----------------------------------------------------------


def test_a_private_source_with_no_token_is_a_configuration_problem():
    settings = settings_for(
        LIBRARY_SOURCES='[{"id":"company","url":"https://github.com/a/b.git","private":true}]'
    )

    assert any("marked private, but neither LIBRARY_READ_TOKEN" in p for p in settings.problems())
    assert settings.library_token is None


def test_either_token_reads_and_the_read_only_one_is_preferred():
    sources = '[{"id":"c","url":"https://github.com/a/b.git","private":true}]'

    write_only = settings_for(LIBRARY_SOURCES=sources, LIBRARY_WRITE_TOKEN="w", LIBRARIANS="g")
    both = settings_for(
        LIBRARY_SOURCES=sources, LIBRARY_WRITE_TOKEN="w", LIBRARY_READ_TOKEN="r", LIBRARIANS="g"
    )

    assert write_only.library_token == "w" and write_only.problems() == []
    assert (
        both.library_token == "r"
    ), "least privilege: a deployment that only reads uses the read token"


# -- reading old versions ---------------------------------------------------


def test_old_versions_are_fetched_with_the_token_too(monkeypatch, tmp_path):
    """The contents of an old version are on the git host, and a private
    one wants to know who is asking."""
    seen = {}

    def read(root, relative, limit, env):
        seen.update(root=root, env=env)
        return "history"

    monkeypatch.setattr("app.library.history.read_history", read)
    service = LibraryService([PRIVATE], tmp_path, token=TOKEN)
    service._checkouts = {"company": tmp_path}
    service._snapshot = Snapshot(
        sources=(SourceStatus("company", "Company", commit="abc"),), parts_source="company"
    )

    assert service.history("parts/1102/1102-0001.yaml") == "history"
    assert seen["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"


def test_a_public_source_s_old_versions_are_fetched_without_it(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        "app.library.history.read_history",
        lambda root, relative, limit, env: seen.setdefault("env", env),
    )
    service = LibraryService([PUBLIC], tmp_path, token=TOKEN)
    service._checkouts = {"kicad": tmp_path}
    service._snapshot = Snapshot(
        sources=(SourceStatus("kicad", "KiCad", commit="abc"),), parts_source="kicad"
    )

    service.history("parts/1102/1102-0001.yaml")

    assert seen["env"] == {}


def test_history_before_the_first_index_is_unavailable_not_an_error(tmp_path):
    with pytest.raises(HistoryUnavailable, match="not been indexed"):
        LibraryService([PRIVATE], tmp_path).history("parts/x.yaml")

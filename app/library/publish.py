"""Committing a change to the library, on someone's behalf.

The library is authored in git, and most of the people who should be able to
add to it would rather not write YAML and push it themselves. So the app does
that part: it writes the files, commits them under the name of whoever asked,
and pushes.

The commit's author is the person. The committer is the app, so the history
shows both who wanted the change and that it came through here.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from app.library.sources import Source, SourceError, _git


class PublishError(Exception):
    """A change could not be committed or pushed."""


class PushRejected(PublishError):
    """The repository moved on while the change was being made.

    Somebody else pushed first. Nothing is wrong with the change; it has to be
    made again on top of theirs.
    """


class NothingToPublish(PublishError):
    """The change left every file as it was."""


@dataclass(frozen=True)
class Identity:
    name: str
    email: str


@dataclass(frozen=True)
class Publisher:
    """How this deployment writes to the library's repository."""

    committer: Identity
    # A token with write access to the repository. Without one the app can
    # read the library but not change it. An origin on the local filesystem
    # needs none, which is what the tests push to.
    token: str | None = None
    # What the git host wants as the user name beside a token. GitHub and
    # Gitea both accept this one.
    token_user: str = "x-access-token"

    def _credentials(self) -> dict[str, str]:
        """The token as git configuration held in the environment.

        Not in the remote's URL and not in an argument: both are readable by
        other processes, and a URL is quoted back in git's error messages.
        """
        if not self.token:
            return {}
        basic = base64.b64encode(f"{self.token_user}:{self.token}".encode()).decode()
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        }

    def publish(self, checkout: Path, source: Source, message: str, author: Identity) -> str:
        """Commit everything changed in `checkout` and push it.

        Returns the new commit. Whatever goes wrong, the checkout is left on
        the commit it started from with nothing uncommitted in it, so the
        index being served still describes what is on disk.
        """
        start = _git("rev-parse", "HEAD", cwd=checkout)
        try:
            _git("add", "--all", cwd=checkout)
            if not _git("status", "--porcelain", cwd=checkout):
                raise NothingToPublish("nothing was changed")

            _git(
                "-c", f"user.name={self.committer.name}",
                "-c", f"user.email={self.committer.email}",
                "commit", "--quiet",
                "--author", f"{author.name} <{author.email}>",
                "--message", message,
                cwd=checkout,
            )  # fmt: skip

            try:
                _git(
                    "push", "--quiet", "origin", f"HEAD:refs/heads/{source.ref}",
                    cwd=checkout, env=self._credentials(),
                )  # fmt: skip
            except SourceError as exc:
                # Git says why in the middle of what it prints, not at the end.
                said = exc.output.lower()
                if "[rejected]" in said or "fetch first" in said or "non-fast-forward" in said:
                    raise PushRejected("the repository changed while this was being saved") from exc
                raise PublishError(str(exc)) from exc

            return _git("rev-parse", "HEAD", cwd=checkout)
        except SourceError as exc:
            self._undo(checkout, start)
            raise PublishError(str(exc)) from exc
        except PublishError:
            self._undo(checkout, start)
            raise

    def discard(self, checkout: Path) -> None:
        """Throw away whatever is uncommitted in a checkout."""
        self._undo(checkout, "HEAD")

    @staticmethod
    def _undo(checkout: Path, commit: str) -> None:
        try:
            _git("reset", "--hard", commit, cwd=checkout)
            _git("clean", "-fd", cwd=checkout)
        except SourceError:
            pass  # the next sync resets the checkout again anyway

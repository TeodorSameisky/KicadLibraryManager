"""Library sources: which repositories, and keeping a checkout of each.

A source is one git repository. Several are supported because a company
library, the official KiCad libraries and a vendor's are separate repositories
with separate owners and lifecycles.

Clones keep the whole history of commits but not the contents of the files
in it. Only the working tree is read when indexing, and for a repository
carrying 3D models the old versions of those files would be most of its size.
The history itself -- who changed which part, and when -- is small, and is
what a part's history page reads. The contents of an old version are fetched
from the git host only when somebody asks for one.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

GIT_TIMEOUT_SECONDS = 600

_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class SourceError(Exception):
    """A source is misconfigured, or could not be cloned or updated."""

    # Everything git printed, for a caller that needs more than its last line.
    output: str = ""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    ref: str = "main"
    # Whether the repository needs a token to be read. Said per source, and
    # false unless said otherwise, so that a token for one host is never sent
    # to another: a deployment may read the official KiCad libraries from
    # one host and its own library from another.
    private: bool = False


@dataclass(frozen=True)
class Checkout:
    """A source's working tree on disk, at a known commit."""

    source: Source
    path: Path
    commit: str


def _source(raw: object) -> Source:
    if not isinstance(raw, Mapping):
        raise SourceError(f"each source must be an object, not {raw!r}")

    missing = [key for key in ("id", "url") if not raw.get(key)]
    if missing:
        raise SourceError(f"source is missing {', '.join(missing)}: {dict(raw)!r}")

    source_id = str(raw["id"])
    if not _SOURCE_ID.match(source_id):
        raise SourceError(
            f"source id {source_id!r} may only contain letters, digits, - and _: "
            "it names the directory the repository is cloned into"
        )

    return Source(
        id=source_id,
        name=str(raw.get("name") or source_id),
        url=str(raw["url"]),
        ref=str(raw.get("ref") or "main"),
        private=bool(raw.get("private")),
    )


def parse_sources(text: str | None) -> tuple[Source, ...]:
    """Read the LIBRARY_SOURCES setting: a JSON array of repositories."""
    if not text or not text.strip():
        return ()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError(f"not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise SourceError("must be a JSON array of sources")

    sources = tuple(_source(item) for item in parsed)

    seen: set[str] = set()
    for source in sources:
        if source.id in seen:
            raise SourceError(f"duplicate source id {source.id!r}")
        seen.add(source.id)
    return sources


# -- git -------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> str:
    """Run git, returning what it printed or raising with its own verdict.

    `env` adds to the environment. It is how a credential reaches git without
    appearing in its arguments, where any process on the machine could read it.
    """
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            # Never stop to ask for a password: there is nobody to type one.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
        )
    except FileNotFoundError as exc:
        raise SourceError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git {args[0]} timed out after {GIT_TIMEOUT_SECONDS}s") from exc

    if done.returncode != 0:
        # The last line is git's own verdict; what precedes it is progress.
        output = (done.stderr or done.stdout or "").strip()
        lines = output.splitlines()
        error = SourceError(f"git {args[0]} failed: {lines[-1] if lines else 'unknown error'}")
        error.output = output
        raise error
    return done.stdout.strip()


def credentials(url: str, token: str | None, user: str = "x-access-token") -> dict[str, str]:
    """git configuration, in the environment, that sends a token to one host.

    In the environment and not in the remote's address or an argument, where
    other processes could read it and git would quote it back in an error.
    Scoped to the host the address names, so it is not sent to a different
    host if the repository redirects there. Only ever sent over https: over
    anything else it would cross the network where anyone could read it.

    GitHub and Gitea both accept `x-access-token` as the user beside a token.
    """
    if not token:
        return {}
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return {}

    scope = f"https://{parts.hostname}{f':{parts.port}' if parts.port else ''}/"
    basic = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{scope}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _hint(error: SourceError, source: Source, token: str | None) -> SourceError:
    """Add to a failed clone or fetch what is likely to be wrong, when git's
    own words would send the reader looking in the wrong place."""
    said = error.output.lower()
    refused = "terminal prompts disabled" in said or "authentication failed" in said
    missing = "repository not found" in said or "returned error: 404" in said

    if source.private and token and (refused or missing or "error: 403" in said):
        advice = "the token was not accepted, or may not read this repository"
    elif not source.private and (refused or missing):
        advice = (
            'if the repository is private, mark the source "private": true in '
            "LIBRARY_SOURCES and set LIBRARY_READ_TOKEN"
        )
    elif source.private and not token and (refused or missing):
        advice = (
            "the source is private, but neither LIBRARY_READ_TOKEN nor LIBRARY_WRITE_TOKEN is set"
        )
    else:
        return error

    hinted = SourceError(f"{error} ({advice})")
    hinted.output = error.output
    return hinted


def _clone(source: Source, target: Path, env: Mapping[str, str] | None = None) -> None:
    # A host that does not support filtering sends everything, which is only
    # slower: git says so and carries on.
    _git(
        "clone", "--filter=blob:none", "--no-tags", "--branch", source.ref,
        source.url, str(target), env=env,
    )  # fmt: skip


def _update(source: Source, target: Path, env: Mapping[str, str] | None = None) -> None:
    # A clone made before history was kept has one commit and no past. It is
    # replaced, not deepened: deepening would download every old version of
    # every file, which is the cost the filter exists to avoid.
    if _git("rev-parse", "--is-shallow-repository", cwd=target) == "true":
        raise SourceError("the clone has no history")

    # The configured URL may have changed since this clone was made. Without
    # this the fetch quietly keeps pulling from the old repository.
    _git("remote", "set-url", "origin", source.url, cwd=target)
    _git("fetch", "--no-tags", "origin", source.ref, cwd=target, env=env)
    # Brings in any file contents the working tree now needs, which with the
    # filter above are fetched here and not in the fetch.
    _git("reset", "--hard", "FETCH_HEAD", cwd=target, env=env)
    # A reset leaves behind files that were deleted upstream but untracked here.
    _git("clean", "-fd", cwd=target)


def _remove(path: Path) -> None:
    """Delete a directory tree, including git's read-only object files."""

    def retry_writable(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=retry_writable)


def sync(source: Source, workdir: Path, token: str | None = None) -> Checkout:
    """Clone `source` under `workdir`, or bring an existing clone up to date.

    `token` is used only if the source is marked private.

    A clone that cannot be updated is replaced rather than repaired: an
    interrupted clone would otherwise leave the platform broken until someone
    deleted the directory by hand. The replacement is cloned alongside and
    only swapped in once it is complete, so an unreachable git host costs
    nothing -- the files the current index points at stay where they are.
    """
    target = workdir / source.id
    workdir.mkdir(parents=True, exist_ok=True)
    env = credentials(source.url, token) if source.private else {}

    if (target / ".git").is_dir():
        try:
            _update(source, target, env)
        except SourceError:
            pass  # fall through to a fresh clone
        else:
            return Checkout(source, target, _git("rev-parse", "HEAD", cwd=target))

    incoming = workdir / f".{source.id}.incoming"
    _remove(incoming)
    try:
        _clone(source, incoming, env)
    except SourceError as exc:
        _remove(incoming)
        raise _hint(exc, source, token) from exc

    _remove(target)
    incoming.rename(target)
    return Checkout(source, target, _git("rev-parse", "HEAD", cwd=target))

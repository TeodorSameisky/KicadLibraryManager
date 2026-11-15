"""Library sources: which repositories, and keeping a checkout of each.

A source is one git repository. Several are supported because a company
library, the official KiCad libraries and a vendor's are separate repositories
with separate owners and lifecycles.

Clones are shallow. Only the working tree is ever read, so history would be
dead weight -- and for a repository carrying 3D models, most of its size.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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


def _clone(source: Source, target: Path) -> None:
    _git("clone", "--depth", "1", "--branch", source.ref, source.url, str(target))


def _update(source: Source, target: Path) -> None:
    # The configured URL may have changed since this clone was made. Without
    # this the fetch quietly keeps pulling from the old repository.
    _git("remote", "set-url", "origin", source.url, cwd=target)
    _git("fetch", "--depth", "1", "origin", source.ref, cwd=target)
    _git("reset", "--hard", "FETCH_HEAD", cwd=target)
    # A reset leaves behind files that were deleted upstream but untracked here.
    _git("clean", "-fd", cwd=target)


def _remove(path: Path) -> None:
    """Delete a directory tree, including git's read-only object files."""

    def retry_writable(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=retry_writable)


def sync(source: Source, workdir: Path) -> Checkout:
    """Clone `source` under `workdir`, or bring an existing clone up to date.

    A clone that cannot be updated is replaced rather than repaired: an
    interrupted clone would otherwise leave the platform broken until someone
    deleted the directory by hand. The replacement is cloned alongside and
    only swapped in once it is complete, so an unreachable git host costs
    nothing -- the files the current index points at stay where they are.
    """
    target = workdir / source.id
    workdir.mkdir(parents=True, exist_ok=True)

    if (target / ".git").is_dir():
        try:
            _update(source, target)
        except SourceError:
            pass  # fall through to a fresh clone
        else:
            return Checkout(source, target, _git("rev-parse", "HEAD", cwd=target))

    incoming = workdir / f".{source.id}.incoming"
    _remove(incoming)
    try:
        _clone(source, incoming)
    except SourceError:
        _remove(incoming)
        raise

    _remove(target)
    incoming.rename(target)
    return Checkout(source, target, _git("rev-parse", "HEAD", cwd=target))

"""A part's history: what was changed in it, by whom, and when.

The library is in git, so the history is already written down. This reads it
back: the commits that touched a part's file, and for each one what the part
looked like before and after, put into words. A raw diff of YAML says which
lines moved; "Tolerance: 1% to 5%" says what happened.

Reading old versions needs the repository to have its history, which a clone
made before this existed does not (see `sources`). That is reported as
unavailable, never guessed at: a shallow clone has a single commit, and
showing it would claim every part was created at once.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from app.library.sources import SourceError, _git

DEFAULT_LIMIT = 50

_RECORD = "\x1e"
_FIELD = "\x1f"
_SSH_URL = re.compile(r"^[\w.-]+@(?P<host>[\w.-]+):(?P<path>[\w./-]+?)(?:\.git)?$")

# Stands for a version of the file that could not be read as a part.
_UNREADABLE: Any = object()


class HistoryUnavailable(Exception):
    """The history cannot be read. The message says why, in words for a reader."""


@dataclass(frozen=True)
class Change:
    commit: str
    when: float  # epoch seconds
    author: str
    subject: str
    what: tuple[str, ...]  # what changed in the part, one sentence each
    note: str = ""  # what the person said about it, if they did: the message's body


@dataclass(frozen=True)
class History:
    changes: tuple[Change, ...]  # newest first
    more: bool = False  # there are older changes than these


# -- describing a change ----------------------------------------------------


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _sources(data: Mapping[str, Any]) -> dict[str, bool]:
    """Manufacturer parts by number, with whether each is the preferred one."""
    found: dict[str, bool] = {}
    for entry in data.get("mpns") or []:
        if isinstance(entry, str):
            found[entry] = False
        elif isinstance(entry, dict) and entry.get("mpn"):
            found[str(entry["mpn"])] = bool(entry.get("preferred"))
    return found


def _quote(value: str) -> str:
    return f'"{value}"' if value else "empty"


def describe(old: Any, new: Any) -> tuple[str, ...]:
    """What changed between two versions of a part's data, in sentences.

    Either may be None (the file did not exist) or _UNREADABLE.
    """
    if old is _UNREADABLE or new is _UNREADABLE:
        return ("The file could not be read as a part at this point.",)
    if old is None and new is None:
        return ()

    if old is None:
        made = [f"Created as {_text(new.get('status')) or 'draft'}."]
        if new.get("description"):
            made.append(f"Description: {new['description']}")
        return tuple(made)
    if new is None:
        return ("Deleted.",)

    said: list[str] = []

    for key, label in (
        ("status", "Status"),
        ("description", "Description"),
        ("symbol", "Symbol"),
        ("footprint", "Footprint"),
    ):
        before, after = _text(old.get(key)), _text(new.get(key))
        if before != after:
            said.append(f"{label}: {_quote(before)} → {_quote(after)}")

    before_fields = {str(k): _text(v) for k, v in (old.get("fields") or {}).items()}
    after_fields = {str(k): _text(v) for k, v in (new.get("fields") or {}).items()}
    # In the order of the names, so the same change reads the same every time.
    for name in sorted(before_fields.keys() | after_fields.keys()):
        if name not in before_fields:
            said.append(f"Added {name}: {after_fields[name]}")
        elif name not in after_fields:
            said.append(f"Removed {name} (it was {before_fields[name]})")
        elif before_fields[name] != after_fields[name]:
            said.append(f"{name}: {_quote(before_fields[name])} → {_quote(after_fields[name])}")

    before_sources, after_sources = _sources(old), _sources(new)
    for mpn in sorted(before_sources.keys() | after_sources.keys()):
        if mpn not in before_sources:
            said.append(f"Added source {mpn}")
        elif mpn not in after_sources:
            said.append(f"Removed source {mpn}")
        elif after_sources[mpn] and not before_sources[mpn]:
            said.append(f"{mpn} is now the preferred source")
        elif before_sources[mpn] and not after_sources[mpn]:
            said.append(f"{mpn} is no longer the preferred source")

    # Anything else a hand-written file carries, said once and not in detail.
    known = {"ipn", "description", "status", "symbol", "footprint", "fields", "mpns"}
    for key in sorted((old.keys() | new.keys()) - known):
        if old.get(key) != new.get(key):
            said.append(f"{key} changed")

    # What the part is, then what it carries, then who may supply it. Stable,
    # so within each the order above is kept.
    return tuple(sorted(said, key=_order))


def _order(line: str) -> int:
    for rank, prefix in enumerate(("Status", "Description", "Symbol", "Footprint")):
        if line.startswith(prefix + ":"):
            return rank
    if line.startswith("Added source") or line.startswith("Removed source"):
        return 8
    if "preferred source" in line:
        return 9
    return 5


# -- reading the repository -------------------------------------------------


def _load(text: str) -> Any:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return _UNREADABLE
    return data if isinstance(data, dict) else _UNREADABLE


def _version(repository: Path, revision: str, path: str, env: Mapping[str, str] | None) -> Any:
    """The part's data as of a commit, or None if the file was not there."""
    try:
        return _load(_git("show", f"{revision}:{path}", cwd=repository, env=env))
    except SourceError as exc:
        said = exc.output.lower()
        if "does not exist" in said or "exists on disk, but not in" in said:
            return None
        raise


def read_history(
    repository: Path,
    path: str,
    limit: int = DEFAULT_LIMIT,
    env: Mapping[str, str] | None = None,
) -> History:
    """The changes to one file, newest first, each put into words.

    `path` is relative to the repository. Raises HistoryUnavailable if the
    repository cannot give a history. `env` carries the credentials of a
    private repository: the contents of an old version are fetched from the
    git host, and the host wants to know who is asking.
    """
    try:
        shallow = _git("rev-parse", "--is-shallow-repository", cwd=repository)
    except SourceError as exc:
        raise HistoryUnavailable("the library is not a git repository on this server") from exc
    if shallow == "true":
        raise HistoryUnavailable(
            "the library was fetched without its history; it is fetched again, in full, "
            "the next time the library is refreshed"
        )

    try:
        # Asked for first and the answer thrown away: with the file contents
        # left on the git host, this is what fetches the versions the diffs
        # need in one request, instead of one request for each below.
        _git(
            "log", "-p", "--format=%H", f"--max-count={limit + 1}", "--", path,
            cwd=repository, env=env,
        )  # fmt: skip
        listing = _git(
            "log",
            # The body can run to several lines, so it is closed with a separator
            # of its own, and the file list that follows is told apart from it.
            f"--format={_RECORD}%H{_FIELD}%at{_FIELD}%an{_FIELD}%s{_FIELD}%b{_FIELD}",
            "--name-status",
            f"--max-count={limit + 1}",
            "--",
            path,
            cwd=repository,
        )
    except SourceError as exc:
        raise HistoryUnavailable(f"git could not list the history: {exc}") from exc

    entries = [record for record in listing.split(_RECORD) if record.strip()]
    changes: list[Change] = []
    for record in entries[:limit]:
        sha, when, author, subject, note, status = record.split(_FIELD, 5)
        kind = status.strip()[:1]  # A added, M modified, D deleted

        try:
            old = None if kind == "A" else _version(repository, f"{sha}^", path, env)
            new = None if kind == "D" else _version(repository, sha, path, env)
        except SourceError as exc:
            raise HistoryUnavailable(f"git could not read an earlier version: {exc}") from exc

        what = describe(old, new)
        changes.append(Change(sha, float(when), author, subject, what, note.strip()))

    return History(changes=tuple(changes), more=len(entries) > limit)


def commit_url(source_url: str, commit: str) -> str | None:
    """Where the git host shows a commit, if it can be worked out.

    GitHub and Gitea both use `<repository>/commit/<sha>`. Built from the host
    and the path only: a configured URL may carry a token in front of the
    host, and that must never end up in a page.
    """
    ssh = _SSH_URL.match(source_url)
    if ssh is not None:
        return f"https://{ssh.group('host')}/{ssh.group('path')}/commit/{commit}"

    parts = urlsplit(source_url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port else ""
    path = parts.path.rstrip("/").removesuffix(".git")
    return f"https://{parts.hostname}{port}{path}/commit/{commit}"

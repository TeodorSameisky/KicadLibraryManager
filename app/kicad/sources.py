"""Library sources: where repositories come from and how they are kept current.

A source is one git repository. The platform supports several because a
company library, the official KiCad libraries and a vendor library are
separate repositories with separate owners and lifecycles.

Clones are shallow. Only the working tree is ever read, so history would be
dead weight -- and for a repository carrying 3D models that is most of its
size.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CLONE_TIMEOUT_SECONDS = 600


class SourceError(Exception):
    """A source could not be cloned, fetched or read."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    ref: str = "main"

    @staticmethod
    def from_dict(raw: dict) -> "Source":
        missing = [k for k in ("id", "url") if not raw.get(k)]
        if missing:
            raise SourceError(f"source is missing {', '.join(missing)}: {raw!r}")

        source_id = str(raw["id"])
        if not source_id.replace("-", "").replace("_", "").isalnum():
            raise SourceError(
                f"source id {source_id!r} must be alphanumeric, - or _: it is used "
                "in paths and in the library names sent to KiCad"
            )

        return Source(
            id=source_id,
            name=str(raw.get("name") or source_id),
            url=str(raw["url"]),
            ref=str(raw.get("ref") or "main"),
        )


def load_sources(raw: str | None = None) -> list[Source]:
    """Parse the source list from JSON, defaulting to LIBRARY_SOURCES."""
    text = raw if raw is not None else os.environ.get("LIBRARY_SOURCES", "")
    if not text.strip():
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError(f"LIBRARY_SOURCES is not valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise SourceError("LIBRARY_SOURCES must be a JSON array of sources")

    sources = [Source.from_dict(item) for item in parsed]

    seen: set[str] = set()
    for source in sources:
        if source.id in seen:
            raise SourceError(f"duplicate source id {source.id!r}")
        seen.add(source.id)
    return sources


@dataclass(frozen=True)
class SyncResult:
    source: Source
    path: Path
    commit: str
    updated: bool


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SourceError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git {args[0]} timed out after {CLONE_TIMEOUT_SECONDS}s") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise SourceError(f"git {args[0]} failed: {detail[-1] if detail else 'unknown error'}")
    return proc.stdout.strip()


def sync(source: Source, workdir: Path) -> SyncResult:
    """Clone or update `source` under `workdir`, returning the checked-out commit.

    A directory that is not a usable git repository is removed and recloned
    rather than repaired: an interrupted clone otherwise leaves the platform
    permanently broken with no way out but manual intervention.
    """
    target = workdir / source.id
    workdir.mkdir(parents=True, exist_ok=True)

    if target.exists() and not (target / ".git").is_dir():
        shutil.rmtree(target)

    if not target.exists():
        _git("clone", "--depth", "1", "--branch", source.ref, source.url, str(target))
        return SyncResult(source, target, _git("rev-parse", "HEAD", cwd=target), updated=True)

    try:
        before = _git("rev-parse", "HEAD", cwd=target)
        # The configured URL may have changed since this clone was made; without
        # this the fetch silently keeps pulling from the old repository.
        _git("remote", "set-url", "origin", source.url, cwd=target)
        _git("fetch", "--depth", "1", "origin", source.ref, cwd=target)
        _git("reset", "--hard", "FETCH_HEAD", cwd=target)
        # Files deleted upstream survive a reset if they are untracked here.
        _git("clean", "-fd", cwd=target)
        after = _git("rev-parse", "HEAD", cwd=target)
        return SyncResult(source, target, after, updated=before != after)
    except SourceError:
        shutil.rmtree(target, ignore_errors=True)
        _git("clone", "--depth", "1", "--branch", source.ref, source.url, str(target))
        return SyncResult(source, target, _git("rev-parse", "HEAD", cwd=target), updated=True)


def changed_files(path: Path, old_commit: str, new_commit: str) -> list[str] | None:
    """Paths changed between two commits, or None if the range is unavailable.

    Shallow clones frequently lack the older commit, in which case the caller
    must fall back to a full reindex rather than assume nothing changed.
    """
    if old_commit == new_commit:
        return []
    try:
        out = _git("diff", "--name-only", f"{old_commit}..{new_commit}", cwd=path)
    except SourceError:
        return None
    return [line for line in out.splitlines() if line]

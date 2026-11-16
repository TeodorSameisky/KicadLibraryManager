"""Changing the library's list of manufacturers: one entry, written back.

Whether the entry makes sense -- that its name and spellings are not another
manufacturer's -- has been decided by `app.services.manufacturers` before any
of this is called. `LibraryService.change` wraps the write in the commit and
the push.

The file is written the way it is meant to be read: a name, then the other
ways it gets written on one line. Whatever else an entry holds is carried
along. A comment in a hand-written list does not survive being edited here,
and an entry with no name, which was an error to begin with, is not kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.library import files
from app.library.editing import EditError


def _entries(root: Path) -> list[dict[str, Any]]:
    """The list as the file has it, each entry as a mapping with a name.

    A library with no list yet has an empty one. A file that is there and is
    not a list is refused: writing over it would throw away whatever it is.
    """
    path = files.manufacturers_file(root)
    if not path.exists():
        return []
    try:
        data = files.read(path)
    except files.Unreadable:
        data = None
    if not isinstance(data, list):
        raise EditError(
            ["manufacturers.yaml cannot be read as a list; put it right in the repository first."]
        )

    entries: list[dict[str, Any]] = []
    for entry in data:
        if isinstance(entry, str):  # the shorthand: a name, and no other spellings
            entry = {"name": entry}
        if isinstance(entry, dict) and str(entry.get("name") or "").strip():
            entries.append({**entry, "name": str(entry["name"]).strip()})
    return entries


def _position(entries: Sequence[dict[str, Any]], name: str) -> int:
    for index, entry in enumerate(entries):
        if entry["name"] == name:
            return index
    raise EditError([f"There is no manufacturer {name} in the list."])


def _save(root: Path, entries: Sequence[dict[str, Any]]) -> None:
    files.write(files.manufacturers_file(root), files.manufacturers_text(entries))


def write_manufacturer(root: Path, was: str | None, name: str, aliases: Sequence[str]) -> str:
    """Add a manufacturer, or change the one called `was`.

    Returns the commit's subject.
    """
    entries = _entries(root)

    if was is None:
        entry: dict[str, Any] = {"name": name}
        # Where it belongs by name, if the list is kept in order; at the end
        # if it is not, so that somebody's own order is left as it is.
        names = [listed["name"].casefold() for listed in entries]
        ordered = names == sorted(names)
        at = sum(listed < name.casefold() for listed in names) if ordered else len(entries)
        entries.insert(at, entry)
        subject = f"Add manufacturer {name}"
    else:
        entry = entries[_position(entries, was)]
        entry["name"] = name
        subject = (
            f"Change manufacturer {name}" if was == name else f"Rename manufacturer {was} to {name}"
        )

    if aliases:
        entry["aliases"] = list(aliases)
    else:
        entry.pop("aliases", None)

    _save(root, entries)
    return subject


def delete_manufacturer(root: Path, name: str) -> str:
    """Take a manufacturer off the list. Returns the commit's subject."""
    entries = _entries(root)
    del entries[_position(entries, name)]
    _save(root, entries)
    return f"Remove manufacturer {name}"

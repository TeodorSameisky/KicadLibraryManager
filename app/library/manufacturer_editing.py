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

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.library import files
from app.library.editing import EditError
from app.library.parts import load_parts


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


# -- the manufacturer parts that write a listed name another way ----------------

# The line of a manufacturer part's file that says who makes it: at the left
# margin, a value, and perhaps a comment after it.
_MADE_BY = re.compile(
    r"""^(?P<key>manufacturer[ \t]*:[ \t]*)
        (?P<value>'(?:[^']|'')*'|"(?:[^"\\]|\\.)*"|[^#\r\n]*?)
        (?P<rest>[ \t]*(?:[ \t]\#[^\r\n]*)?)(?P<end>\r?)$""",
    re.MULTILINE | re.VERBOSE,
)


def _with_name(text: str, name: str) -> str | None:
    """A manufacturer part's file with its maker's name replaced and nothing
    else touched -- not its comments, not its line endings. None if the line
    cannot be found as one line, which a file written by hand may manage."""
    found = _MADE_BY.findall(text)
    if len(found) != 1:
        return None
    return _MADE_BY.sub(
        lambda line: f"{line['key']}{files._scalar(name)}{line['rest']}{line['end']}", text
    )


def _rename_maker(path: Path, written: str, name: str) -> None:
    try:
        with path.open(encoding="utf-8", newline="") as file:  # line endings as they are
            text = file.read()
        before = files.parse(text)
    except (OSError, UnicodeDecodeError, files.Unreadable) as exc:
        raise EditError([f"{path.name} cannot be read: {exc}"]) from exc
    if not isinstance(before, dict) or str(before.get("manufacturer") or "").strip() != written:
        raise EditError([f"{path.name} no longer says it is made by {written}."])

    wanted = {**before, "manufacturer": name}
    after = _with_name(text, name)
    try:
        kept = after is not None and files.parse(after) == wanted
    except files.Unreadable:
        kept = False
    if after is not None and kept:
        with path.open("w", encoding="utf-8", newline="") as file:
            file.write(after)
    else:
        # Not a line this can change in place. Written out whole, then: its
        # comments are lost, and what it says is right.
        files.write(path, files.block(wanted))


def use_listed_names(root: Path, only: str | None = None) -> tuple[str, list[str]]:
    """Rewrite every manufacturer part that names a listed manufacturer by
    another spelling, so that its file says the name the list gives -- for
    one manufacturer, or for all of them.

    Goes by the files as they are in `root` and not by any index made
    earlier: what is rewritten is what is wrong now. Returns the commit's
    subject and the manufacturer parts that were rewritten.
    """
    index = load_parts(root)
    rewritten: list[str] = []
    for mpn in sorted(index.mpns.values(), key=lambda mpn: mpn.mpn):
        if not mpn.written_otherwise or index.manufacturers.resolve(mpn.manufacturer) is None:
            continue
        if only is not None and mpn.manufacturer != only:
            continue
        _rename_maker(root / mpn.path, mpn.manufacturer_written, mpn.manufacturer)
        rewritten.append(mpn.mpn)

    if not rewritten:
        whose = f"made by {only}" if only is not None else "in the library"
        raise EditError(
            [f"Every manufacturer part {whose} already names its maker as the list does."]
        )
    parts = "1 manufacturer part" if len(rewritten) == 1 else f"{len(rewritten)} manufacturer parts"
    if only is not None:
        return f"Write {only} by its listed name in {parts}", rewritten
    return f"Write manufacturers by their listed names in {parts}", rewritten


def delete_manufacturer(root: Path, name: str) -> str:
    """Take a manufacturer off the list. Returns the commit's subject."""
    entries = _entries(root)
    del entries[_position(entries, name)]
    _save(root, entries)
    return f"Remove manufacturer {name}"

"""Changing parts: what a form asks for, checked and written as library files.

Three things can be done. Anyone may request a part, which adds it as a draft.
A draft can be corrected. A librarian approves it, or retires it later.

Each is two steps kept apart: `check` decides whether the request makes sense
against the library as indexed, and the `write_*` functions put it on disk in
a checkout. Nothing here touches git; `LibraryService.change` wraps a write in
the commit and the push.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.library.parts import IPN_PATTERN, LIFECYCLES, PART_STATUSES, mpn_file_stem
from app.library.refs import LibraryRef
from app.library.snapshot import Snapshot

MAX_FIELDS = 40
MAX_SOURCES = 20


class EditError(ValueError):
    """What was asked for cannot be done. One sentence per thing to fix."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class SourceEntry:
    """A manufacturer part, as entered against an internal part."""

    mpn: str
    preferred: bool = False
    # Only used when the library does not have this manufacturer part yet.
    manufacturer: str = ""
    datasheet: str = ""
    lifecycle: str = "active"


@dataclass(frozen=True)
class PartForm:
    description: str
    symbol: str
    footprint: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    sources: tuple[SourceEntry, ...] = ()
    category: str = ""  # which numbers to take the next one from; new parts only


# -- checking ---------------------------------------------------------------


def check(form: PartForm, snapshot: Snapshot, *, new: bool, category: str | None = None) -> None:
    """Raise EditError with everything that is wrong, not just the first.

    `category` is the category of the part being edited. A new part says its
    own.
    """
    problems: list[str] = []
    category = form.category if new else category

    if new and form.category not in snapshot.parts.categories:
        problems.append("Choose a category.")
    if not form.description.strip():
        problems.append("Give the part a description; the number alone cannot be searched for.")

    symbol = LibraryRef.parse(form.symbol)
    if symbol is None:
        problems.append("Choose a symbol.")
    else:
        try:
            snapshot.catalog.symbol_ancestry(symbol)
        except LookupError:
            problems.append(f"The library has no symbol {form.symbol}.")
        except ValueError as exc:
            problems.append(f"The symbol {form.symbol} cannot be placed: {exc}.")

    if form.footprint:
        footprint = LibraryRef.parse(form.footprint)
        if footprint is None or snapshot.catalog.footprint(footprint) is None:
            problems.append(f"The library has no footprint {form.footprint}.")

    if len(form.fields) > MAX_FIELDS:
        problems.append(f"A part can carry at most {MAX_FIELDS} fields.")
    if any(not name.strip() for name in form.fields):
        problems.append("Every field needs a name.")

    # What every part in this category has to say about itself.
    template = snapshot.parts.templates.get(category or "")
    if template is not None:
        problems.extend(template.problems(form.fields))

    if len(form.sources) > MAX_SOURCES:
        problems.append(f"A part can list at most {MAX_SOURCES} manufacturer parts.")
    names = [entry.mpn for entry in form.sources]
    if any(not name.strip() for name in names):
        problems.append("Every manufacturer part needs its part number.")
    if len(set(names)) != len(names):
        problems.append("A manufacturer part is listed twice.")
    if sum(entry.preferred for entry in form.sources) > 1:
        problems.append("Only one manufacturer part can be the preferred one.")

    for entry in form.sources:
        if entry.mpn in snapshot.parts.mpns:
            continue  # already in the library; what the form says about it is not used
        if entry.lifecycle not in LIFECYCLES:
            problems.append(f"{entry.mpn}: the lifecycle must be one of {', '.join(LIFECYCLES)}.")
        if entry.datasheet and not entry.datasheet.startswith(("https://", "http://")):
            problems.append(f"{entry.mpn}: the datasheet must be a web address.")

    if problems:
        raise EditError(problems)


# -- writing ----------------------------------------------------------------


def _block(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _part_text(data: dict[str, Any]) -> str:
    """A part as the library's files are laid out: what it is, what it is
    drawn with, what it carries, who makes it -- a blank line between each."""
    known = ("ipn", "description", "status", "symbol", "footprint", "fields", "mpns")
    blocks = [
        _block({key: data[key] for key in ("ipn", "description", "status") if key in data}),
        _block({key: data[key] for key in ("symbol", "footprint") if data.get(key)}),
    ]
    if data.get("fields"):
        blocks.append(_block({"fields": data["fields"]}))
    if data.get("mpns"):
        blocks.append(_block({"mpns": data["mpns"]}))
    # Anything else a hand-written file holds is carried along, not dropped.
    extra = {key: value for key, value in data.items() if key not in known}
    if extra:
        blocks.append(_block(extra))
    return "\n".join(block for block in blocks if block.strip() != "{}")


def _part_path(root: Path, ipn: str) -> Path:
    """Where a part's file is, wherever under parts/ it was filed."""
    found = sorted((root / "parts").rglob(f"{ipn}.yaml"))
    if not found:
        raise EditError([f"There is no part {ipn}."])
    return found[0]


def _read(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EditError([f"{path.name} cannot be read as a part."])
    return data


def next_ipn(root: Path, category: str) -> str:
    """The next free number in a category, as wide as the ones before it."""
    taken = [
        match.group("number")
        for match in (IPN_PATTERN.match(path.stem) for path in (root / "parts").rglob("*.yaml"))
        if match and match.group("category") == category
    ]
    width = max([4, *(len(number) for number in taken)])
    following = max((int(number) for number in taken), default=0) + 1
    return f"{category}-{following:0{width}d}"


def _apply(data: dict[str, Any], form: PartForm, root: Path) -> None:
    """Put a form's answers into a part's data, and add any new MPN files."""
    data["description"] = form.description.strip()
    data["symbol"] = form.symbol
    data["footprint"] = form.footprint
    # A field left empty is not one the part carries. Written as "", it would
    # blank the symbol's own value on the sheet.
    data["fields"] = {
        name.strip(): value.strip() for name, value in form.fields.items() if value.strip()
    }
    data["mpns"] = [
        {"mpn": entry.mpn, "preferred": True} if entry.preferred else {"mpn": entry.mpn}
        for entry in form.sources
    ]

    for entry in form.sources:
        path = root / "mpns" / f"{mpn_file_stem(entry.mpn)}.yaml"
        if path.exists():
            continue  # one manufacturer part serves many parts; this form does not edit it
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"mpn": entry.mpn, "manufacturer": entry.manufacturer.strip()}
        if entry.datasheet:
            document["datasheet"] = entry.datasheet.strip()
        document["lifecycle"] = entry.lifecycle
        path.write_text(_block(document), encoding="utf-8", newline="\n")


def write_new_part(root: Path, form: PartForm) -> tuple[str, str]:
    """Add a draft under the next number in its category.

    Returns the number it was given and the commit message. The number is
    taken from the files in `root`, which the caller has just brought up to
    date, so two requests made together cannot be given the same one.
    """
    ipn = next_ipn(root, form.category)
    data: dict[str, Any] = {"ipn": ipn, "status": "draft"}
    _apply(data, form, root)

    path = root / "parts" / form.category / f"{ipn}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_part_text(data), encoding="utf-8", newline="\n")
    return ipn, f"Request {ipn}: {data['description']}"


def write_part(root: Path, ipn: str, form: PartForm) -> str:
    """Change an existing part, keeping its number and its status."""
    path = _part_path(root, ipn)
    data = _read(path)
    _apply(data, form, root)
    path.write_text(_part_text(data), encoding="utf-8", newline="\n")
    return f"Edit {ipn}: {data['description']}"


def write_status(root: Path, ipn: str, status: str) -> str:
    """Approve a part, retire it, or send it back to being a draft."""
    if status not in PART_STATUSES:
        raise EditError([f"The status must be one of {', '.join(PART_STATUSES)}."])

    path = _part_path(root, ipn)
    data = _read(path)
    if data.get("status", "draft") == status:
        raise EditError([f"{ipn} is already {status}."])
    data["status"] = status
    path.write_text(_part_text(data), encoding="utf-8", newline="\n")

    verb = {"approved": "Approve", "obsolete": "Retire", "draft": "Return to draft"}[status]
    return f"{verb} {ipn}: {data.get('description', '')}".rstrip(": ")

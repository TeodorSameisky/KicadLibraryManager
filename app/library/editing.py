"""Changing parts: what a form asks for, checked and put into the library's files.

Three things can be done. Anyone may request a part, which adds it as a draft.
A draft can be corrected. A librarian approves it, or retires it later.

Each is two steps kept apart: `check` decides whether the request makes sense
against the library as indexed, and the `write_*` functions put it on disk in
a checkout. Nothing here touches git; `LibraryService.change` wraps a write in
the commit and the push.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain import rules
from app.domain.parts import IPN_PATTERN, Lifecycle, PartStatus, is_web_address
from app.domain.rules import Rule
from app.errors import Invalid
from app.library import files
from app.library.snapshot import Snapshot

MAX_FIELDS = 40
MAX_SOURCES = 20


class EditError(Invalid):
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
    lifecycle: str = Lifecycle.ACTIVE.value


@dataclass(frozen=True)
class PartForm:
    description: str
    symbol: str
    footprint: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    sources: tuple[SourceEntry, ...] = ()
    category: str = ""  # which numbers to take the next one from; new parts only


# -- checking ---------------------------------------------------------------

# What a part falls short of, as the person asking for it is told. The same
# rules the library's own parts are held to, where they are listed as issues;
# here they are reasons to refuse, said to someone who can put them right.
_SENTENCES: dict[Rule, str] = {
    Rule.NO_DESCRIPTION: "Give the part a description; the number alone cannot be searched for.",
    Rule.NO_SYMBOL: "Choose a symbol.",
    Rule.UNQUALIFIED_SYMBOL: "Choose a symbol.",
    Rule.MISSING_SYMBOL: "The library has no symbol {symbol}.",
    Rule.UNPLACEABLE: "The symbol {symbol} cannot be placed: {detail}.",
    Rule.UNQUALIFIED_FOOTPRINT: "The library has no footprint {footprint}.",
    Rule.MISSING_FOOTPRINT: "The library has no footprint {footprint}.",
    Rule.TEMPLATE: "{detail}",
    Rule.MULTIPLE_PREFERRED: "Only one manufacturer part can be the preferred one.",
}


def check(form: PartForm, snapshot: Snapshot, *, new: bool, category: str | None = None) -> None:
    """Raise EditError with everything that is wrong, not just the first.

    `category` is the category of the part being edited. A new part says its
    own.
    """
    problems: list[str] = []
    category = form.category if new else category

    if new and form.category not in snapshot.parts.categories:
        problems.append("Choose a category.")

    # What is asked of any part, whether it is being requested or is already
    # in the library.
    for finding in rules.part_findings(
        description=form.description,
        symbol=form.symbol,
        footprint=form.footprint,
        fields=form.fields,
        preferred=[entry.preferred for entry in form.sources],
        template=snapshot.parts.templates.get(category or ""),
        assets=snapshot.catalog,
    ):
        problems.append(
            _SENTENCES[finding.rule].format(
                symbol=form.symbol, footprint=form.footprint, detail=finding.detail
            )
        )

    # And what is asked of a request as such: that it can be written down.
    if len(form.fields) > MAX_FIELDS:
        problems.append(f"A part can carry at most {MAX_FIELDS} fields.")
    if any(not name.strip() for name in form.fields):
        problems.append("Every field needs a name.")

    if len(form.sources) > MAX_SOURCES:
        problems.append(f"A part can list at most {MAX_SOURCES} manufacturer parts.")
    names = [entry.mpn for entry in form.sources]
    if any(not name.strip() for name in names):
        problems.append("Every manufacturer part needs its part number.")
    if len(set(names)) != len(names):
        problems.append("A manufacturer part is listed twice.")

    for entry in form.sources:
        if entry.mpn in snapshot.parts.mpns:
            continue  # already in the library; what the form says about it is not used
        if entry.lifecycle not in Lifecycle:
            problems.append(f"{entry.mpn}: the lifecycle must be one of {', '.join(Lifecycle)}.")
        if entry.datasheet and not is_web_address(entry.datasheet):
            problems.append(f"{entry.mpn}: the datasheet must be a web address.")

    if problems:
        raise EditError(problems)


# -- writing ----------------------------------------------------------------


def _part_path(root: Path, ipn: str) -> Path:
    path = files.find_part(root, ipn)
    if path is None:
        raise EditError([f"There is no part {ipn}."])
    return path


def _read(path: Path) -> dict[str, Any]:
    try:
        data = files.read(path)
    except files.Unreadable:
        data = None
    if not isinstance(data, dict):
        raise EditError([f"{path.name} cannot be read as a part."])
    return data


def next_ipn(root: Path, category: str) -> str:
    """The next free number in a category, as wide as the ones before it."""
    taken = [
        match.group("number")
        for match in (IPN_PATTERN.match(path.stem) for path in files.part_files(root))
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
        path = files.mpn_file(root, entry.mpn)
        if path.exists():
            continue  # one manufacturer part serves many parts; this form does not edit it
        files.write(
            path,
            files.mpn_text(
                mpn=entry.mpn,
                manufacturer=entry.manufacturer.strip(),
                datasheet=entry.datasheet.strip(),
                lifecycle=entry.lifecycle,
            ),
        )


MAX_NOTE = 1000


def with_note(subject: str, note: str = "") -> str:
    """A commit message: the subject the app wrote, then what the person said.

    The note is theirs and goes in as written, except that what could not be
    printed is dropped and the lines are tidied. It is the body of the message,
    where git shows it under the subject and the history page shows it under
    the change.
    """
    lines = note.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    text = "\n".join("".join(c for c in line if c.isprintable()).rstrip() for line in lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"{subject}\n\n{text}" if text else subject


def write_new_part(root: Path, form: PartForm) -> tuple[str, str]:
    """Add a draft under the next number in its category.

    Returns the number it was given and the commit message. The number is
    taken from the files in `root`, which the caller has just brought up to
    date, so two requests made together cannot be given the same one.
    """
    ipn = next_ipn(root, form.category)
    data: dict[str, Any] = {"ipn": ipn, "status": PartStatus.DRAFT.value}
    _apply(data, form, root)

    files.write(files.part_file(root, form.category, ipn), files.part_text(data))
    return ipn, f"Request {ipn}: {data['description']}"


def write_part(root: Path, ipn: str, form: PartForm) -> str:
    """Change an existing part, keeping its number and its status."""
    path = _part_path(root, ipn)
    data = _read(path)
    _apply(data, form, root)
    files.write(path, files.part_text(data))
    return f"Edit {ipn}: {data['description']}"


# What a change of status is called in the library's history.
_VERBS = {
    PartStatus.APPROVED: "Approve",
    PartStatus.OBSOLETE: "Retire",
    PartStatus.DRAFT: "Return to draft",
}


def write_status(root: Path, ipn: str, status: str) -> str:
    """Approve a part, retire it, or send it back to being a draft."""
    if status not in PartStatus:
        raise EditError([f"The status must be one of {', '.join(PartStatus)}."])

    path = _part_path(root, ipn)
    data = _read(path)
    if data.get("status", PartStatus.DRAFT) == status:
        raise EditError([f"{ipn} is already {status}."])
    data["status"] = PartStatus(status).value
    files.write(path, files.part_text(data))

    verb = _VERBS[PartStatus(status)]
    return f"{verb} {ipn}: {data.get('description', '')}".rstrip(": ")

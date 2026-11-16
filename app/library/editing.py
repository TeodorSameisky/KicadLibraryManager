"""Changing parts: what a form asks for, put into the library's files.

Three things can be done to a part's file: a new one is added as a draft, an
existing one is changed, and its status is set. Each takes a checkout and
returns the subject of the commit that will record it.

Whether what was asked makes sense is not decided here: `app.services.parts`
has checked it against the library before any of these is called. What is
refused here is what only the files can say -- that there is no such part,
or that its file cannot be read. Nothing here touches git either;
`LibraryService.change` wraps a write in the commit and the push.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.forms import PartForm
from app.domain.parts import IPN_PATTERN, PartStatus
from app.errors import Invalid
from app.library import files


class EditError(Invalid):
    """What was asked for cannot be done. One sentence per thing to fix."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


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


def write_new_part(root: Path, form: PartForm) -> tuple[str, str]:
    """Add a draft under the next number in its category.

    Returns the number it was given and the commit's subject. The number is
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

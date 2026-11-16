"""Requesting a part, correcting it, and approving it.

Anyone may request a part: it is added as a draft under the next number in
its category. A draft may be corrected by anyone. Once a librarian has
approved it, only a librarian changes it.

Every one of these ends in a commit to the library's repository, made in the
name of whoever asked.
"""

from __future__ import annotations

from pathlib import Path

from app.domain import policy
from app.domain.parts import Part
from app.domain.policy import Actor
from app.errors import NotAllowed
from app.library import editing
from app.library.editing import PartForm
from app.library.service import LibraryService
from app.services import changes


def require_may_edit(actor: Actor, part: Part) -> None:
    if not policy.may_edit(actor, part):
        raise NotAllowed("Only a librarian can change a part that is not a draft.")


async def request_part(
    library: LibraryService, form: PartForm, *, note: str = "", actor: Actor
) -> str:
    """Add a part as a draft, under the next number in its category.

    Returns the number it was given.
    """
    changes.require_writable(library)
    editing.check(form, library.snapshot, new=True)

    given: dict[str, str] = {}

    def write(root: Path) -> str:
        # The number is taken here, from the files as they are once the
        # repository has been brought up to date, and not before.
        given["ipn"], message = editing.write_new_part(root, form)
        return editing.with_note(message, note)

    await changes.publish(library, write, actor)
    return given["ipn"]


async def edit_part(
    library: LibraryService, part: Part, form: PartForm, *, note: str = "", actor: Actor
) -> None:
    """Change a part, keeping its number and its status."""
    changes.require_writable(library)
    require_may_edit(actor, part)
    editing.check(form, library.snapshot, new=False, category=part.category)

    await changes.publish(
        library,
        lambda root: editing.with_note(editing.write_part(root, part.ipn, form), note),
        actor,
    )


async def set_status(
    library: LibraryService, part: Part, status: str, *, note: str = "", actor: Actor
) -> None:
    """Approve a part, retire it, or send it back to being a draft."""
    changes.require_writable(library)
    if not policy.may_decide(actor):
        raise NotAllowed("Only a librarian can do this.")

    await changes.publish(
        library,
        lambda root: editing.with_note(editing.write_status(root, part.ipn, status), note),
        actor,
    )

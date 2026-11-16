"""Changing what a category asks of its parts.

A template decides what every part in a category must say about itself, so
all of this is a librarian's to do. Each change is one commit to the library's
repository, in the name of whoever made it, like any other change to it.
"""

from __future__ import annotations

from app.domain import policy
from app.domain.policy import Actor
from app.errors import NotAllowed, NotFound
from app.library import editing as part_editing
from app.library import template_editing as editing
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.library.template_editing import Shortfall, TemplateForm
from app.services import changes


def require_category(snapshot: Snapshot, category: str) -> str:
    if category not in snapshot.parts.categories:
        raise NotFound(f"There is no category {category}")
    return category


def _require_may_change(library: LibraryService, category: str, actor: Actor) -> None:
    if not policy.may_edit_templates(actor):
        raise NotAllowed("Only a librarian can do this.")
    require_category(library.snapshot, category)
    changes.require_writable(library)


async def save_template(
    library: LibraryService, category: str, form: TemplateForm, *, note: str = "", actor: Actor
) -> Shortfall:
    """Create a category's template, or replace it.

    Returns what the saved template leaves the existing parts short of.
    """
    _require_may_change(library, category, actor)
    snapshot = library.snapshot
    editing.check_template(form, snapshot, category)

    name = snapshot.category_name(category)
    after = await changes.publish(
        library,
        lambda root: part_editing.with_note(
            editing.write_template(root, category, name, form), note
        ),
        actor,
    )
    return editing.shortfall(after, category)


async def remove_template(
    library: LibraryService, category: str, *, note: str = "", actor: Actor
) -> Shortfall:
    """Take a template away: the category's parts are then asked for nothing."""
    _require_may_change(library, category, actor)

    name = library.snapshot.category_name(category)
    after = await changes.publish(
        library,
        lambda root: part_editing.with_note(editing.delete_template(root, category, name), note),
        actor,
    )
    return editing.shortfall(after, category)

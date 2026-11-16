"""Changing what a category asks of its parts.

A template decides what every part in a category must say about itself, so
all of this is a librarian's to do. Each change is checked against the
library as it stands (`check_template`) and is then one commit to the
library's repository, in the name of whoever made it, like any other change
to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import policy
from app.domain.forms import TemplateForm
from app.domain.policy import Actor
from app.domain.templates import RESERVED_FIELDS, placeholders
from app.errors import NotAllowed, NotFound
from app.library import template_editing as editing
from app.library.editing import EditError
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import changes

MAX_FIELDS = 40
MAX_OPTIONS = 60


@dataclass(frozen=True)
class Shortfall:
    """What a saved template leaves the library's existing parts short of."""

    parts: int  # how many parts in the category now fall short of it
    of: int  # out of how many parts there are in the category


def require_category(snapshot: Snapshot, category: str) -> str:
    if category not in snapshot.parts.categories:
        raise NotFound(f"There is no category {category}")
    return category


# -- whether a request makes sense --------------------------------------------


def check_template(form: TemplateForm, snapshot: Snapshot, category: str) -> None:
    """Raise EditError with everything that is wrong, not just the first."""
    problems: list[str] = []

    if category not in snapshot.parts.categories:
        problems.append(f"There is no category {category}; it has to be in categories.yaml first.")

    if len(form.fields) > MAX_FIELDS:
        problems.append(f"A template can name at most {MAX_FIELDS} fields.")

    seen: set[str] = set()
    for spec in form.fields:
        name = spec.name.strip()
        if not name:
            problems.append("Every field needs a name.")
            continue
        if name.lower() in seen:
            problems.append(f"{name} is listed twice.")
        seen.add(name.lower())

        if name in RESERVED_FIELDS:
            problems.append(f"{name} is set from the part itself and cannot be a template field.")

        if len(spec.options) > MAX_OPTIONS:
            problems.append(f"{name} can have at most {MAX_OPTIONS} options.")
        if len(set(spec.options)) != len(spec.options):
            problems.append(f"{name} lists an option twice.")
        if spec.options and spec.default and spec.default not in spec.options:
            problems.append(f"{name}: the default {spec.default!r} is not one of its options.")

    # A pattern that names a field the template does not have would build a
    # Value with a hole in it, for every part.
    names = {spec.name.strip() for spec in form.fields}
    for used in placeholders(form.value):
        if used not in names:
            problems.append(f"The value pattern uses {{{used}}}, which is not one of the fields.")

    if problems:
        raise EditError(problems)


def shortfall(snapshot: Snapshot, category: str) -> Shortfall:
    """How many existing parts in a category fall short of its template."""
    template = snapshot.parts.templates.get(category)
    parts = [part for part in snapshot.parts.parts.values() if part.category == category]
    falling = sum(1 for part in parts if template and template.problems(part.fields))
    return Shortfall(parts=falling, of=len(parts))


# -- the changes ----------------------------------------------------------------


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
    check_template(form, snapshot, category)

    name = snapshot.category_name(category)
    after = await changes.publish(
        library,
        lambda root: editing.write_template(root, category, name, form),
        actor,
        note=note,
    )
    return shortfall(after, category)


async def remove_template(
    library: LibraryService, category: str, *, note: str = "", actor: Actor
) -> Shortfall:
    """Take a template away: the category's parts are then asked for nothing."""
    _require_may_change(library, category, actor)

    name = library.snapshot.category_name(category)
    after = await changes.publish(
        library,
        lambda root: editing.delete_template(root, category, name),
        actor,
        note=note,
    )
    return shortfall(after, category)

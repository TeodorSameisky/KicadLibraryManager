"""Changing a category's template: checked, and written as a library file.

A template decides what every part in a category must say about itself, so
changing one is a librarian's to do. The same two steps as for a part: `check`
decides whether the request makes sense, and `write_template` puts it on disk
in a checkout. `LibraryService.change` wraps it in the commit and the push.

Templates are written the way they are meant to be read, in the order the
fields should appear on the form. A comment in a hand-written template does
not survive being edited here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.templates import RESERVED_FIELDS, Template, TemplateField, placeholders
from app.library import files
from app.library.editing import EditError
from app.library.snapshot import Snapshot

MAX_FIELDS = 40
MAX_OPTIONS = 60

_CATEGORY = re.compile(r"^[A-Za-z0-9]+$")


@dataclass(frozen=True)
class TemplateForm:
    """A template as someone asks for it to be, before it has been checked."""

    fields: tuple[TemplateField, ...] = ()
    value: str = ""


@dataclass(frozen=True)
class Shortfall:
    """What a saved template leaves the library's existing parts short of."""

    parts: int  # how many parts in the category now fall short of it
    of: int  # out of how many parts there are in the category


def _path(root: Path, category: str) -> Path:
    # The category becomes a file name, so it is held to what one can be.
    if not _CATEGORY.match(category):
        raise EditError([f"{category!r} is not a category code."])
    return files.template_file(root, category)


# -- checking ---------------------------------------------------------------


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


# -- writing ----------------------------------------------------------------


def write_template(root: Path, category: str, name: str, form: TemplateForm) -> str:
    """Create or replace a category's template. Returns the commit message."""
    path = _path(root, category)
    existed = path.exists()
    files.write(path, files.template_text(category, form.value, form.fields))
    verb = "Change" if existed else "Add"
    return f"{verb} the template for {category}: {name}"


def delete_template(root: Path, category: str, name: str) -> str:
    """Remove a category's template, after which it asks nothing of its parts."""
    path = _path(root, category)
    if not path.exists():
        raise EditError([f"There is no template for {category}."])
    path.unlink()
    return f"Remove the template for {category}: {name}"


def form_from(template: Template | None) -> dict[str, Any]:
    """What the template form starts out holding."""
    if template is None:
        return {"value": "", "fields": []}
    return {
        "value": template.value,
        "fields": [
            {
                "name": spec.name,
                "required": spec.required,
                "options": list(spec.options),
                "example": spec.example,
                "default": spec.default,
            }
            for spec in template.fields
        ],
    }


__all__ = [
    "Shortfall",
    "TemplateForm",
    "check_template",
    "delete_template",
    "form_from",
    "shortfall",
    "write_template",
]

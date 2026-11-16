"""Changing a category's template: what a form asks for, written as a library file.

Whether the template makes sense has been decided by `app.services.templates`
before either of these is called. `LibraryService.change` wraps the write in
the commit and the push.

Templates are written the way they are meant to be read, in the order the
fields should appear on the form. A comment in a hand-written template does
not survive being edited here.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain.forms import TemplateForm
from app.library import files
from app.library.editing import EditError

_CATEGORY = re.compile(r"^[A-Za-z0-9]+$")


def _path(root: Path, category: str) -> Path:
    # The category becomes a file name, so it is held to what one can be.
    if not _CATEGORY.match(category):
        raise EditError([f"{category!r} is not a category code."])
    return files.template_file(root, category)


def write_template(root: Path, category: str, name: str, form: TemplateForm) -> str:
    """Create or replace a category's template. Returns the commit's subject."""
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

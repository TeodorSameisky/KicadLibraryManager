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

import yaml

from app.library.editing import EditError
from app.library.parts import Template
from app.library.snapshot import Snapshot

MAX_FIELDS = 40
MAX_OPTIONS = 60

# Set by the platform from the part itself, wherever the part is placed. A
# template field with one of these names would fight with that.
RESERVED_FIELDS = ("IPN", "Datasheet", "Footprint", "Description", "MPN", "Manufacturer")

_CATEGORY = re.compile(r"^[A-Za-z0-9]+$")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool = False
    options: tuple[str, ...] = ()
    example: str = ""
    default: str = ""


@dataclass(frozen=True)
class TemplateForm:
    fields: tuple[FieldSpec, ...] = ()
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
    return root / "templates" / f"{category}.yaml"


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
    for used in dict.fromkeys(match.strip() for match in _PLACEHOLDER.findall(form.value)):
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


# Text that something reading YAML might take for a number: digits, with a
# sign, a decimal point or an exponent. "0805" is one of these, and a reader
# that does not follow this parser's rules would take it for 805.
_NUMBER_LIKE = re.compile(r"^[-+]?[0-9][0-9_.]*([eE][-+]?[0-9]+)?$")


class _Dumper(yaml.SafeDumper):
    """Quotes what could be mistaken for a number, whichever parser reads it."""


def _represent_text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "'" if _NUMBER_LIKE.match(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_text)


def _scalar(value: object) -> str:
    """One value as YAML writes it on a line: quoted only where it has to be.

    "0402" is quoted, because bare it could be read as the number 402, and
    "1%" is not, because nothing can mistake it for anything else.
    """
    dumped = yaml.dump(
        value, Dumper=_Dumper, default_flow_style=True, allow_unicode=True, width=10_000
    )
    return dumped.strip().removesuffix("...").strip()


def template_text(category: str, form: TemplateForm) -> str:
    """The file: which category, how Value is built, then the fields.

    Written by hand rather than dumped, so that each field is a short block
    and its options are on one line, as a person would write them:
    `options: [0.1%, 1%, 5%]`.
    """
    lines = [f"category: {_scalar(category)}"]
    if form.value.strip():
        lines.append(f"value: {_scalar(form.value.strip())}")

    if form.fields:
        lines += ["", "fields:"]
    for spec in form.fields:
        lines.append(f"  - name: {_scalar(spec.name.strip())}")
        if spec.required:
            lines.append("    required: true")
        if spec.options:
            lines.append(f"    options: {_scalar(list(spec.options))}")
        if spec.example.strip():
            lines.append(f"    example: {_scalar(spec.example.strip())}")
        if spec.default.strip():
            lines.append(f"    default: {_scalar(spec.default.strip())}")
    return "\n".join(lines) + "\n"


def write_template(root: Path, category: str, name: str, form: TemplateForm) -> str:
    """Create or replace a category's template. Returns the commit message."""
    path = _path(root, category)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template_text(category, form), encoding="utf-8", newline="\n")
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
    "RESERVED_FIELDS",
    "FieldSpec",
    "Shortfall",
    "TemplateForm",
    "check_template",
    "delete_template",
    "form_from",
    "shortfall",
    "template_text",
    "write_template",
]

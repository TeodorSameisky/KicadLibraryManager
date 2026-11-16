"""What a category expects every part in it to say about itself.

A resistor without a resistance is not a part anyone can choose. The template
is where a category says so, once, instead of each part being left to remember
it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

# Set by the platform from the part itself, wherever the part is placed. A
# template field with one of these names would fight with that.
RESERVED_FIELDS = ("IPN", "Datasheet", "Footprint", "Description", "MPN", "Manufacturer")

# A field named in a Value pattern: "{Resistance}". Names in braces and
# nothing more.
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def placeholders(pattern: str) -> tuple[str, ...]:
    """The fields a Value pattern is built from, each once, in order."""
    return tuple(dict.fromkeys(name.strip() for name in _PLACEHOLDER.findall(pattern)))


@dataclass(frozen=True)
class TemplateField:
    name: str
    required: bool = False
    options: tuple[str, ...] = ()  # the only values allowed, if any are given
    example: str = ""  # shown in the empty field, to say what belongs there
    default: str = ""  # what the field starts out holding


@dataclass(frozen=True)
class Template:
    category: str
    fields: tuple[TemplateField, ...] = ()
    # What KiCad's own Value field should show, built from the part's fields:
    # "{Resistance}". Only used for a part that sets no Value of its own.
    value: str = ""

    def problems(self, fields: Mapping[str, str]) -> list[str]:
        """What a part's fields are missing or have wrong, as sentences."""
        found = []
        for expected in self.fields:
            given = fields.get(expected.name, "").strip()
            if not given:
                if expected.required:
                    found.append(f"{expected.name} is required.")
            elif expected.options and given not in expected.options:
                found.append(
                    f"{expected.name} must be one of {', '.join(expected.options)}; "
                    f"it is {given!r}."
                )
        return found

    def value_for(self, fields: Mapping[str, str]) -> str:
        """The Value this template builds from a part's fields, or ""."""
        built = _PLACEHOLDER.sub(lambda match: fields.get(match.group(1).strip(), ""), self.value)
        return " ".join(built.split())

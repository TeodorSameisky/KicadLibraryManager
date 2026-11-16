"""What someone asks a part or a template to be, before anyone has checked it.

These are requests, not parts: whatever a form or a script sent, with
nothing yet said about whether it makes sense. `app.services` decides that,
against the library as it stands, and `app.library` writes down what was
accepted. Both need the same shape for it, which is why it is here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.parts import Lifecycle
from app.domain.templates import TemplateField


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
    """A part as someone asks for it to be."""

    description: str
    symbol: str
    footprint: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    sources: tuple[SourceEntry, ...] = ()
    category: str = ""  # which numbers to take the next one from; new parts only


@dataclass(frozen=True)
class TemplateForm:
    """A category's template as someone asks for it to be."""

    fields: tuple[TemplateField, ...] = ()
    value: str = ""

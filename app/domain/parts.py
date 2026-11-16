"""Internal part numbers and the manufacturer parts that satisfy them.

An IPN is what gets placed in KiCad. It names a symbol and a footprint from
the asset libraries, carries the field values, and lists the manufacturer
parts approved for it.

MPNs are things of their own rather than entries inside each IPN, because one
MPN can satisfy several IPNs. That is also what makes obsolescence
answerable: a lifecycle change is one edit, and the reverse lookup says which
parts it reaches.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

IPN_PATTERN = re.compile(r"^(?P<category>[A-Za-z0-9]+)-(?P<number>\d+)$")


class PartStatus(StrEnum):
    """Where a part stands. In the order a part goes through them."""

    DRAFT = "draft"  # asked for, and still anyone's to correct
    APPROVED = "approved"  # a librarian has said it may be designed in
    OBSOLETE = "obsolete"  # retired: not for new designs


class Lifecycle(StrEnum):
    """What a manufacturer says about one of its parts."""

    ACTIVE = "active"
    NRND = "nrnd"  # not recommended for new designs
    OBSOLETE = "obsolete"
    UNKNOWN = "unknown"


def is_web_address(text: str) -> bool:
    """Whether a value out of a library file is something a page can link to.

    Anything that is not a web address -- a `javascript:` URL above all -- is
    shown as text instead, and is not accepted from a form.
    """
    return text.startswith(("https://", "http://"))


@dataclass(frozen=True)
class Category:
    code: str
    name: str


@dataclass(frozen=True)
class Mpn:
    mpn: str
    manufacturer: str = ""
    description: str = ""
    datasheet: str = ""
    # One of `Lifecycle`, unless the file says something else: that is
    # reported as an issue, and the part is still shown as it was written.
    lifecycle: str = Lifecycle.UNKNOWN.value

    @property
    def datasheet_url(self) -> str | None:
        """The datasheet, if it is something a page can safely link to."""
        return self.datasheet if is_web_address(self.datasheet) else None


@dataclass(frozen=True)
class MpnRef:
    """An IPN's mention of a manufacturer part."""

    mpn: str
    preferred: bool = False


@dataclass(frozen=True)
class Part:
    ipn: str
    description: str = ""
    # One of `PartStatus`, unless the file says something else. A library is
    # written by hand and always has mistakes in it, so a part with one is
    # kept, reported, and shown with the status it was given.
    status: str = PartStatus.DRAFT.value
    symbol: str = ""
    footprint: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)
    mpns: tuple[MpnRef, ...] = ()
    path: str = ""  # relative to the repository it came from

    @property
    def category(self) -> str:
        match = IPN_PATTERN.match(self.ipn)
        return match.group("category") if match else ""

    @property
    def preferred_mpn(self) -> str | None:
        """The marked source, or the first listed when none is marked."""
        for ref in self.mpns:
            if ref.preferred:
                return ref.mpn
        return self.mpns[0].mpn if self.mpns else None

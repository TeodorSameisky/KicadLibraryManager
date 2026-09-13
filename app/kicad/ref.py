"""A reference to an asset in a library: "Passives:R_0603_1608Metric".

Its own type because the same two lines -- is there a colon, split on the
first one -- were repeated at every point that resolves a symbol, a footprint
or a part's fields, and each copy decided slightly differently what to do with
a malformed one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryRef:
    library: str
    name: str

    @staticmethod
    def parse(raw: str | None) -> LibraryRef | None:
        """Split a qualified reference, or None when it is not one.

        Both halves must be present. A bare name has no library to look in,
        and "Passives:" names nothing -- resolving either would report an
        asset that no source provides, which sends the reader looking for a
        missing file rather than at the typo in front of them.
        """
        if not raw or ":" not in raw:
            return None
        library, _, name = raw.partition(":")
        if not library or not name:
            return None
        return LibraryRef(library=library, name=name)

    def __str__(self) -> str:
        return f"{self.library}:{self.name}"

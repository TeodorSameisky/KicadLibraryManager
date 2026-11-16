"""How a part names a symbol or a footprint."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryRef:
    """A qualified reference to a symbol or footprint: `Passives:R_0603`."""

    library: str
    name: str

    @staticmethod
    def parse(raw: str | None) -> LibraryRef | None:
        """Split a reference on its first colon, or None if it is not one.

        Both halves have to be there. A bare name has no library to look in
        and `Passives:` names nothing; resolving either would report an asset
        that no source provides, and send the reader hunting for a missing
        file instead of at the typo in front of them.
        """
        if not raw:
            return None
        library, separator, name = raw.partition(":")
        if not separator or not library or not name:
            return None
        return LibraryRef(library, name)

    def __str__(self) -> str:
        return f"{self.library}:{self.name}"

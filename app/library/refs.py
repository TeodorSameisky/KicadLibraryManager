"""How one library file names another."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ENV_PREFIX = re.compile(r"^\$\{[^}]+\}/?")


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


def model_key(reference: str) -> str:
    """Reduce a footprint's 3D model path to one relative to a repository.

    Footprints point at models through a variable -- ${KICAD10_3DMODEL_DIR},
    ${KIPRJMOD}, whatever a contributor set up -- that differs per machine and
    means nothing on the server. Only what follows it identifies the model:
    `Passives.3dshapes/R_0603.step`.
    """
    cleaned = reference.strip().replace("\\", "/")
    cleaned = _ENV_PREFIX.sub("", cleaned)
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")

"""How one library file names another."""

from __future__ import annotations

import re

from app.domain.refs import LibraryRef

__all__ = ["LibraryRef", "model_key"]

_ENV_PREFIX = re.compile(r"^\$\{[^}]+\}/?")


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

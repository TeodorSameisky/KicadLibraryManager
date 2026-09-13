"""Symbol and footprint drawings for a part.

Separate from the endpoints that serve them because both the JSON API and the
part page need the same drawings: the API returns them as standalone SVG
documents for the panel's thumbnails, while the page inlines them so that
hovering a pin can highlight the pad it maps to.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.kicad.build import BuildError, build_symbol
from app.kicad.parts import Part
from app.kicad.ref import LibraryRef
from app.kicad.render import render_footprint, render_symbol
from app.kicad.sexpr import ParseError, loads
from app.library_service import Snapshot

log = logging.getLogger(__name__)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def symbol_svg(part: Part, snapshot: Snapshot, public_url: str, remote_prefix: str) -> str | None:
    """Drawn from the payload KiCad receives, so the preview is what lands."""
    try:
        payload = build_symbol(part, snapshot.catalog, snapshot.parts, public_url, remote_prefix)
    except BuildError as exc:
        log.info("no symbol preview for %s: %s", part.ipn, exc)
        return None

    try:
        node = loads(payload.text).child("symbol")
    except ParseError:
        log.warning("built symbol for %s does not parse", part.ipn)
        return None

    return render_symbol(node, f"{part.ipn} {part.description}".strip()) if node else None


def footprint_svg(part: Part, snapshot: Snapshot) -> str | None:
    ref = LibraryRef.parse(part.footprint)
    if ref is None:
        return None

    located = snapshot.catalog.find_footprint(ref.library, ref.name)
    if not located:
        return None

    text = _read(Path(located[0].asset.path))
    if text is None:
        log.warning("footprint file for %s could not be read", part.ipn)
        return None

    try:
        return render_footprint(loads(text), str(ref))
    except ParseError:
        log.warning("footprint %s does not parse", ref)
        return None

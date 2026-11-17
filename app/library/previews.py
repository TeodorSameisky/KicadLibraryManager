"""A part, drawn: its symbol, its land pattern, and where its 3D model is.

Each returns None when there is nothing to show. A part with a preview
missing still belongs in the catalog; why it is missing is in the issue list.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.domain.parts import Part
from app.kicad import symbol as symbol_tree
from app.kicad.render import render_footprint, render_symbol
from app.kicad.sexpr import ParseError, loads
from app.library.assets import BrokenInheritance, Footprint
from app.library.placement import PlacementError, PlacementOptions, placed_symbol
from app.library.refs import LibraryRef, model_key
from app.library.snapshot import Snapshot

log = logging.getLogger(__name__)


def _footprint(part: Part, snapshot: Snapshot) -> Footprint | None:
    ref = LibraryRef.parse(part.footprint)
    return snapshot.catalog.footprint(ref) if ref else None


def symbol_svg(part: Part, snapshot: Snapshot, options: PlacementOptions) -> str | None:
    """Drawn from what KiCad would receive, so the preview is what lands."""
    try:
        symbol = placed_symbol(part, snapshot, options)
    except PlacementError as exc:
        log.info("no symbol preview for %s: %s", part.ipn, exc)
        return None
    return render_symbol(symbol.node, f"{part.ipn} {part.description}".strip())


def footprint_svg(part: Part, snapshot: Snapshot) -> str | None:
    footprint = _footprint(part, snapshot)
    if footprint is None:
        return None
    try:
        tree = loads(footprint.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ParseError) as exc:
        log.warning("no footprint preview for %s: %s", part.ipn, exc)
        return None
    return render_footprint(tree, part.footprint)


def model_path(part: Part, snapshot: Snapshot) -> Path | None:
    """The part's 3D model on disk.

    Reached through the footprint: a symbol never names a model. The
    reference sits in the .kicad_mod, next to its placement offsets.
    """
    footprint = _footprint(part, snapshot)
    if footprint is None:
        return None
    for reference in footprint.model_refs:
        model = snapshot.catalog.model(model_key(reference))
        # Only STEP can be shown; the viewer has no reader for VRML.
        if (
            model is not None
            and not model.is_lfs_pointer
            and model.path.suffix.lower() in (".step", ".stp")
        ):
            return model.path
    return None


# -- an asset on its own, not as a part draws it --------------------------------


def library_symbol_svg(snapshot: Snapshot, ref: LibraryRef) -> str | None:
    """A symbol as the library has it, with what it derives from filled in."""
    try:
        chain = snapshot.catalog.symbol_ancestry(ref)
        nodes = []
        for symbol in reversed(chain):
            node = loads(symbol.path.read_text(encoding="utf-8")).child("symbol")
            if node is None:
                return None
            nodes.append(node)
        return render_symbol(symbol_tree.flatten(nodes, ref.name), str(ref))
    except (LookupError, BrokenInheritance, OSError, UnicodeDecodeError, ParseError) as exc:
        log.info("no preview for symbol %s: %s", ref, exc)
        return None


def library_footprint_svg(footprint: Footprint) -> str | None:
    try:
        tree = loads(footprint.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ParseError) as exc:
        log.warning("no preview for footprint %s:%s: %s", footprint.library, footprint.name, exc)
        return None
    return render_footprint(tree, f"{footprint.library}:{footprint.name}")

"""Symbol and footprint drawings, as standalone SVG documents.

Used for the panel's thumbnails. The part page inlines the same drawings
instead, so that hovering a pin can highlight the pad it maps to -- an
<img>-embedded SVG is a separate document and nothing in the page can reach
into it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.dependencies import get_service, read_asset_text, require_part
from app.config import Settings, get_settings
from app.kicad.build import BuildError, build_symbol
from app.kicad.parts import Part
from app.kicad.render import render_footprint, render_symbol
from app.kicad.sexpr import ParseError, loads
from app.library_service import LibraryService, Snapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["previews"])

SVG_HEADERS = {"Cache-Control": "public, max-age=300"}


def symbol_svg(part: Part, snapshot: Snapshot, settings: Settings) -> str | None:
    """Drawn from the payload KiCad receives, so the preview is what lands."""
    try:
        payload = build_symbol(part, snapshot.catalog, snapshot.parts,
                               settings.public_url, settings.remote_library_prefix)
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
    if not part.footprint or ":" not in part.footprint:
        return None

    library, name = part.footprint.split(":", 1)
    located = snapshot.catalog.find_footprint(library, name)
    if not located:
        return None

    text = read_asset_text(Path(located[0].asset.path))
    if text is None:
        log.warning("footprint file for %s could not be read", part.ipn)
        return None

    try:
        return render_footprint(loads(text), part.footprint)
    except ParseError:
        log.warning("footprint %s does not parse", part.footprint)
        return None


def _svg_response(svg: str) -> Response:
    return Response(content=svg, media_type="image/svg+xml", headers=SVG_HEADERS)


@router.get("/parts/{ipn}/symbol.svg")
async def part_symbol_svg(
    ipn: str,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> Response:
    part = require_part(ipn, service)
    svg = symbol_svg(part, service.snapshot, settings)
    if svg is None:
        raise HTTPException(status_code=409, detail=f"{ipn} has no drawable symbol")
    return _svg_response(svg)


@router.get("/parts/{ipn}/footprint.svg")
async def part_footprint_svg(
    ipn: str,
    service: LibraryService = Depends(get_service),
) -> Response:
    part = require_part(ipn, service)
    svg = footprint_svg(part, service.snapshot)
    if svg is None:
        raise HTTPException(
            status_code=404,
            detail=f"no footprint available for {ipn}",
        )
    return _svg_response(svg)

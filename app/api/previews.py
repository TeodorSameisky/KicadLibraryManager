"""Symbol, footprint and 3D model endpoints.

Standalone SVG documents, used for the panel's thumbnails. The part page
inlines the same drawings instead, so that hovering a pin can highlight the pad
it maps to -- an <img>-embedded SVG is a separate document and nothing in the
page can reach into it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from app.api.dependencies import find_model, get_service, require_part
from app.auth.guard import require_access
from app.config import Settings, get_settings
from app.library_service import LibraryService
from app.previews import footprint_svg, symbol_svg

router = APIRouter(prefix="/api/v1", tags=["previews"], dependencies=[Depends(require_access)])

SVG_HEADERS = {"Cache-Control": "public, max-age=300"}


def _svg_response(svg: str) -> Response:
    return Response(content=svg, media_type="image/svg+xml", headers=SVG_HEADERS)


@router.get("/parts/{ipn}/symbol.svg")
async def part_symbol_svg(
    ipn: str,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> Response:
    part = require_part(ipn, service)
    svg = symbol_svg(part, service.snapshot, settings.public_url, settings.remote_library_prefix)
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
        raise HTTPException(status_code=404, detail=f"no footprint available for {ipn}")
    return _svg_response(svg)


@router.get("/parts/{ipn}/model.step")
async def part_model(
    ipn: str,
    service: LibraryService = Depends(get_service),
) -> FileResponse:
    """The part's 3D model, as the STEP file the library holds.

    Served as-is and tessellated in the browser. Converting here would mean
    carrying a CAD kernel in the image and spending its CPU on every model,
    where the browser does it once and caches the result.
    """
    part = require_part(ipn, service)
    path = find_model(part, service.snapshot)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no 3D model available for {ipn}")

    return FileResponse(
        path,
        media_type="model/step",
        filename=path.name,
        headers={"Cache-Control": "public, max-age=3600"},
    )

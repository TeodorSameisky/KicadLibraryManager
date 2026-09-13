"""Catalog endpoints: index status, part search, and the IPN page.

The IPN page is also the target of the Datasheet field written into every
symbol placed in KiCad, so its URL is derived from the IPN and must stay
stable.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.kicad.assets import build_assets
from app.kicad.build import BuildError, build_symbol
from app.kicad.render import render_symbol
from app.kicad.render_footprint import render_footprint
from app.kicad.sexpr import loads
from app.config import Settings, get_settings
from app.library_service import LibraryService, State

router = APIRouter()


def get_service(request: Request) -> LibraryService:
    return request.app.state.library


def _symbol_svg(part, snapshot, settings) -> str | None:
    try:
        payload = build_symbol(part, snapshot.catalog, snapshot.parts,
                               settings.public_url, settings.remote_library_prefix)
    except BuildError:
        return None
    node = loads(payload.text).child("symbol")
    return render_symbol(node, f"{part.ipn} {part.description}".strip()) if node else None


def _footprint_svg(part, snapshot) -> str | None:
    if not part.footprint or ":" not in part.footprint:
        return None
    library, name = part.footprint.split(":", 1)
    located = snapshot.catalog.find_footprint(library, name)
    if not located:
        return None
    try:
        text = Path(located[0].asset.path).read_text(encoding="utf-8")
    except OSError:
        return None
    return render_footprint(loads(text), part.footprint)


def _part_summary(part, parts_index) -> dict:
    preferred = part.preferred_mpn
    manufacturer = ""
    if preferred and preferred in parts_index.mpns:
        manufacturer = parts_index.mpns[preferred].manufacturer
    return {
        "ipn": part.ipn,
        "description": part.description,
        "status": part.status,
        "preferred_mpn": preferred,
        "manufacturer": manufacturer,
        "mpn_count": len(part.mpns),
    }


@router.get("/api/v1/status")
async def status(service: LibraryService = Depends(get_service)) -> dict:
    snap = service.snapshot
    return {
        "state": service.state.value,
        "error": service.error,
        "parts": snap.part_count(),
        "symbols": snap.catalog.symbol_count(),
        "errors": len(snap.errors),
        "warnings": len(snap.warnings),
        "indexed_at": snap.finished_at,
        "duration_seconds": round(snap.duration, 1),
        "sources": [
            {"id": s.id, "name": s.name, "commit": s.commit,
             "symbols": s.symbols, "parts": s.parts, "error": s.error}
            for s in snap.sources
        ],
    }


@router.get("/api/v1/parts")
async def list_parts(
    q: str = "",
    limit: int = 100,
    service: LibraryService = Depends(get_service),
) -> dict:
    snap = service.snapshot
    if snap.parts is None:
        return {"state": service.state.value, "parts": [], "total": 0}

    found = service.search(q, limit=max(1, min(limit, 500)))
    return {
        "state": service.state.value,
        "total": len(snap.parts.parts),
        "parts": [_part_summary(p, snap.parts) for p in found],
    }


@router.get("/api/v1/parts/{ipn}")
async def get_part(ipn: str, service: LibraryService = Depends(get_service)) -> dict:
    part = service.get_part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"No part {ipn}")

    parts_index = service.snapshot.parts
    sources = []
    for ref in part.mpns:
        mpn = parts_index.mpns.get(ref.mpn)
        sources.append({
            "mpn": ref.mpn,
            "preferred": ref.preferred,
            "manufacturer": mpn.manufacturer if mpn else None,
            "datasheet": mpn.datasheet if mpn else None,
            "lifecycle": mpn.lifecycle if mpn else "unknown",
            "known": mpn is not None,
        })

    return {
        "ipn": part.ipn,
        "description": part.description,
        "status": part.status,
        "symbol": part.symbol,
        "footprint": part.footprint,
        "fields": part.fields,
        "mpns": sources,
    }


@router.get("/api/v1/parts/{ipn}/assets")
async def part_assets(
    ipn: str,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> dict:
    """Everything KiCad needs to place this part, in the order to send it.

    The panel relays these over the RPC bridge; the payloads are inline because
    that is the transport the protocol fully specifies.
    """
    part = service.get_part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"No part {ipn}")

    snapshot = service.snapshot
    try:
        bundle = build_assets(part, snapshot.catalog, snapshot.parts,
                              settings.public_url, settings.remote_library_prefix)
    except BuildError as exc:
        # Refusing beats placing a part with no body or no land pattern.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "ipn": bundle.ipn,
        "warnings": bundle.warnings,
        "assets": [
            {
                "command": a.command,
                "label": a.label,
                "filename": a.filename,
                "parameters": a.parameters,
                "size_bytes": a.size_bytes,
                "data": a.data,
            }
            for a in bundle.assets
        ],
    }


@router.get("/api/v1/parts/{ipn}/symbol.svg")
async def part_symbol_svg(
    ipn: str,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> Response:
    """The symbol drawn as SVG, for previewing a part before placing it.

    Rendered from the same payload KiCad receives, so what is on screen is
    what lands on the schematic.
    """
    part = service.get_part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"No part {ipn}")

    snapshot = service.snapshot
    try:
        payload = build_symbol(part, snapshot.catalog, snapshot.parts,
                               settings.public_url, settings.remote_library_prefix)
    except BuildError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    node = loads(payload.text).child("symbol")
    svg = render_symbol(node, f"{part.ipn} {part.description}".strip())
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/api/v1/parts/{ipn}/footprint.svg")
async def part_footprint_svg(
    ipn: str,
    service: LibraryService = Depends(get_service),
) -> Response:
    """The land pattern drawn as SVG: pads, silkscreen and courtyard."""
    part = service.get_part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"No part {ipn}")
    if not part.footprint or ":" not in part.footprint:
        raise HTTPException(status_code=404, detail=f"{ipn} names no footprint")

    library, name = part.footprint.split(":", 1)
    located = service.snapshot.catalog.find_footprint(library, name)
    if not located:
        raise HTTPException(
            status_code=404,
            detail=f"footprint {part.footprint!r} is not provided by any source")

    try:
        text = Path(located[0].asset.path).read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    svg = render_footprint(loads(text), part.footprint)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=300"})


@router.get("/ipn/{ipn}", response_class=HTMLResponse)
async def ipn_page(
    ipn: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> HTMLResponse:
    """The page a placed symbol's Datasheet field points back to."""
    part = service.get_part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"No part {ipn}")

    parts_index = service.snapshot.parts
    mpns = [
        {
            "ref": ref,
            "detail": parts_index.mpns.get(ref.mpn),
        }
        for ref in part.mpns
    ]

    # Inlined rather than linked: an <img> SVG is a separate document, so its
    # pins could not be linked to the footprint's pads.
    snapshot = service.snapshot
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="ipn.html",
        context={
            "part": part,
            "mpns": mpns,
            "symbol_svg": _symbol_svg(part, snapshot, settings),
            "footprint_svg": _footprint_svg(part, snapshot),
            "root_path": settings.root_path,
            "provider_name": settings.provider_name,
            "indexing": service.state is State.SYNCING,
        },
    )

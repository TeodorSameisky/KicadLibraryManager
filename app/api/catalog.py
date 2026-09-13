"""JSON endpoints for the catalog."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_service, part_sources, part_summary, require_part
from app.auth.guard import require_access
from app.config import Settings, get_settings
from app.kicad.assets import build_assets
from app.kicad.build import BuildError
from app.kicad.parts import PART_STATUSES
from app.library_service import LibraryService

log = logging.getLogger(__name__)

# Every route below reads the library, so the guard is applied to the router
# rather than repeated -- an endpoint added later is closed by default.
router = APIRouter(prefix="/api/v1", tags=["catalog"], dependencies=[Depends(require_access)])

MAX_PAGE = 500

_STATUS_PATTERN = f"^({'|'.join(PART_STATUSES)})$"


@router.get("/status")
async def status(service: LibraryService = Depends(get_service)) -> dict:
    """Index state, per source. What to look at when something seems missing."""
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
        # What the catalog can be filtered by, so the panel can offer filters
        # that reflect the library in front of it rather than a fixed list.
        "facets": snap.facets(),
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "commit": s.commit,
                "symbols": s.symbols,
                "parts": s.parts,
                "error": s.error,
            }
            for s in snap.sources
        ],
    }


@router.get("/issues")
async def issues(
    limit: int = Query(200, ge=1, le=1000),
    severity: str | None = Query(None, pattern="^(error|warning)$"),
    service: LibraryService = Depends(get_service),
) -> dict:
    """Everything that did not resolve, so a library can be repaired.

    Paths are relative to their repository, which is what a maintainer needs
    and says nothing about where the container keeps its clones.
    """
    snap = service.snapshot
    found = snap.issues
    if severity:
        found = [i for i in found if i.severity.value == severity]
    return {
        "total": len(found),
        "errors": len(snap.errors),
        "warnings": len(snap.warnings),
        "returned": min(len(found), limit),
        "issues": [i.as_dict() for i in found[:limit]],
    }


@router.get("/parts")
async def list_parts(
    q: str = Query("", max_length=200),
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    category: str | None = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=MAX_PAGE),
    service: LibraryService = Depends(get_service),
) -> dict:
    snap = service.snapshot
    if snap.parts is None:
        return {
            "state": service.state.value,
            "parts": [],
            "total": 0,
            "matched": 0,
            "returned": 0,
        }

    found = service.search(q, status=status, category=category)
    page = found[:limit]
    return {
        "state": service.state.value,
        # `total` is the catalog; `matched` is what the filters selected. The
        # panel reports "12 of 40 matching" rather than "12 of 4000", which
        # said nothing about the search that produced it.
        "total": len(snap.parts.parts),
        "matched": len(found),
        "returned": len(page),
        "parts": [part_summary(p, snap.parts) for p in page],
    }


@router.get("/parts/{ipn}")
async def get_part(ipn: str, service: LibraryService = Depends(get_service)) -> dict:
    part = require_part(ipn, service)
    return {
        "ipn": part.ipn,
        "description": part.description,
        "status": part.status,
        "category": part.category,
        "symbol": part.symbol,
        "footprint": part.footprint,
        "fields": part.fields,
        "mpns": part_sources(part, service.snapshot.parts),
        "related": [p.ipn for p in service.parts_sharing_mpn(part)],
    }


@router.get("/parts/{ipn}/assets")
async def part_assets(
    ipn: str,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> dict:
    """Everything KiCad needs to place this part, in the order to send it."""
    part = require_part(ipn, service)
    snapshot = service.snapshot

    try:
        bundle = build_assets(
            part,
            snapshot.catalog,
            snapshot.parts,
            settings.public_url,
            settings.remote_library_prefix,
        )
    except BuildError as exc:
        # Refusing beats placing a part with no body or no land pattern. The
        # message names library assets rather than container paths, so it is
        # safe to return and is what the user can act on.
        log.info("cannot build %s: %s", ipn, exc)
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

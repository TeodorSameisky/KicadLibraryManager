"""The catalog as JSON, plus each part's drawings and model.

This is what the panel's scripts call. Every route reads the library, so the
guard sits on the router: a route added later is closed without anyone having
to remember.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.config import Settings
from app.library import previews
from app.library.parts import PART_STATUSES, Part
from app.library.placement import PlacementError, asset_bundle
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.web.deps import get_library, get_settings, get_snapshot, require_access, require_part

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["catalog"], dependencies=[Depends(require_access)])

MAX_PARTS_PER_PAGE = 500
MAX_ISSUES_PER_PAGE = 1000

_STATUS = f"^({'|'.join(PART_STATUSES)})$"


# -- the index --------------------------------------------------------------


@router.get("/status")
async def status(library: LibraryService = Depends(get_library)) -> dict[str, Any]:
    """Where the index stands, source by source: where to look when
    something seems to be missing."""
    snapshot = library.snapshot
    return {
        "state": library.state.value,
        "error": library.error,
        "parts": snapshot.part_count,
        "symbols": snapshot.catalog.symbol_count,
        "footprints": snapshot.catalog.footprint_count,
        "models": snapshot.catalog.model_count,
        "errors": len(snapshot.errors),
        "warnings": len(snapshot.warnings),
        "indexed_at": snapshot.indexed_at,
        "duration_seconds": round(snapshot.duration, 1),
        # What the catalog can be narrowed by, so the panel offers the
        # filters this library supports rather than a fixed list.
        "facets": {
            "statuses": [asdict(facet) for facet in snapshot.status_facets],
            "categories": [asdict(facet) for facet in snapshot.category_facets],
        },
        "sources": [asdict(source) for source in snapshot.sources],
    }


@router.get("/issues")
async def issues(
    severity: str | None = Query(None, pattern="^(error|warning)$"),
    limit: int = Query(200, ge=1, le=MAX_ISSUES_PER_PAGE),
    snapshot: Snapshot = Depends(get_snapshot),
) -> dict[str, Any]:
    """Everything in the library that does not line up."""
    found = {"error": snapshot.errors, "warning": snapshot.warnings}.get(
        severity or "", list(snapshot.issues)
    )
    page = found[:limit]
    return {
        "total": len(found),
        "returned": len(page),
        # The library's totals, not the page's: someone reading the errors
        # can still see that there are warnings to look at.
        "errors": len(snapshot.errors),
        "warnings": len(snapshot.warnings),
        "issues": [
            {
                "severity": issue.severity.value,
                "kind": issue.kind,
                "message": issue.message,
                "path": issue.path,
                "source": issue.source,
            }
            for issue in page
        ],
    }


# -- parts ------------------------------------------------------------------


def _summary(part: Part, snapshot: Snapshot) -> dict[str, Any]:
    preferred = snapshot.mpn(part.preferred_mpn)
    return {
        "ipn": part.ipn,
        "description": part.description,
        "status": part.status,
        "preferred_mpn": part.preferred_mpn,
        "manufacturer": preferred.manufacturer if preferred else "",
        "mpn_count": len(part.mpns),
    }


@router.get("/parts")
async def list_parts(
    q: str = Query("", max_length=200),
    status: str | None = Query(None, pattern=_STATUS),
    category: str | None = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=MAX_PARTS_PER_PAGE),
    library: LibraryService = Depends(get_library),
) -> dict[str, Any]:
    snapshot = library.snapshot
    found = snapshot.search(q, status=status, category=category)
    page = found[:limit]
    return {
        "state": library.state.value,
        # `total` is the catalog and `matched` is what the filters selected.
        # "12 of 4000" says nothing about the search that produced the twelve.
        "total": snapshot.part_count,
        "matched": len(found),
        "returned": len(page),
        "parts": [_summary(part, snapshot) for part in page],
    }


@router.get("/parts/{ipn}")
async def get_part(
    part: Part = Depends(require_part), snapshot: Snapshot = Depends(get_snapshot)
) -> dict[str, Any]:
    sources = []
    for ref in part.mpns:
        mpn = snapshot.mpn(ref.mpn)
        sources.append(
            {
                "mpn": ref.mpn,
                "preferred": ref.preferred,
                "manufacturer": mpn.manufacturer if mpn else None,
                "datasheet": mpn.datasheet if mpn else None,
                "lifecycle": mpn.lifecycle if mpn else "unknown",
                "known": mpn is not None,
            }
        )
    return {
        "ipn": part.ipn,
        "description": part.description,
        "status": part.status,
        "category": part.category,
        "symbol": part.symbol,
        "footprint": part.footprint,
        "fields": dict(part.fields),
        "mpns": sources,
        "related": [other.ipn for other in snapshot.related(part)],
    }


@router.get("/parts/{ipn}/assets")
async def part_assets(
    part: Part = Depends(require_part),
    snapshot: Snapshot = Depends(get_snapshot),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Everything KiCad needs to place this part, in the order to send it."""
    try:
        bundle = asset_bundle(part, snapshot, settings.placement)
    except PlacementError as exc:
        # Refusing beats placing a part with no body. The message names
        # library assets, not server paths, and is what the user can act on.
        log.info("cannot place %s: %s", part.ipn, exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "ipn": bundle.ipn,
        "warnings": bundle.warnings,
        "assets": [asdict(asset) for asset in bundle.assets],
    }


# -- drawings and models ----------------------------------------------------
#
# Standalone documents, for the panel's thumbnails. The part page inlines the
# same drawings instead, so that hovering a pin can light up the pad it maps
# to -- an SVG inside an <img> is a separate document nothing can reach into.


def _svg(markup: str) -> Response:
    return Response(
        content=markup,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/parts/{ipn}/symbol.svg")
async def part_symbol(
    part: Part = Depends(require_part),
    snapshot: Snapshot = Depends(get_snapshot),
    settings: Settings = Depends(get_settings),
) -> Response:
    markup = previews.symbol_svg(part, snapshot, settings.placement)
    if markup is None:
        raise HTTPException(status_code=404, detail=f"{part.ipn} has no drawable symbol")
    return _svg(markup)


@router.get("/parts/{ipn}/footprint.svg")
async def part_footprint(
    part: Part = Depends(require_part), snapshot: Snapshot = Depends(get_snapshot)
) -> Response:
    markup = previews.footprint_svg(part, snapshot)
    if markup is None:
        raise HTTPException(status_code=404, detail=f"{part.ipn} has no drawable footprint")
    return _svg(markup)


@router.get("/parts/{ipn}/model.step")
async def part_model(
    part: Part = Depends(require_part), snapshot: Snapshot = Depends(get_snapshot)
) -> FileResponse:
    """The part's 3D model, as the STEP file the library holds.

    Served as it is and tessellated in the browser. Converting here would put
    a CAD kernel in the image and spend its CPU on every view, where a browser
    does the work once and keeps the result.
    """
    path = previews.model_path(part, snapshot)
    if path is None:
        raise HTTPException(status_code=404, detail=f"{part.ipn} has no 3D model")
    return FileResponse(
        path,
        media_type="model/step",
        filename=path.name,
        headers={"Cache-Control": "private, max-age=3600"},
    )

"""The catalog as JSON, plus each part's drawings and model.

This is what the panel's scripts call. Every route reads the library, so the
guard sits on the router: a route added later is closed without anyone having
to remember.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response

from app.config import Settings
from app.domain import policy
from app.domain.parts import Part, PartStatus
from app.domain.policy import Actor
from app.errors import NotFound
from app.library import previews
from app.library.placement import asset_bundle
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import search
from app.web.deps import (
    get_actor,
    get_library,
    get_settings,
    get_snapshot,
    require_access,
    require_part,
)
from app.web.schemas.library import AssetBundleOut, IndexStatus, IssueList, PartDetail, PartList

router = APIRouter(prefix="/api/v1", tags=["catalog"], dependencies=[Depends(require_access)])

MAX_PARTS_PER_PAGE = 500
MAX_ISSUES_PER_PAGE = 1000

_STATUS = f"^({'|'.join(PartStatus)})$"


# -- the index --------------------------------------------------------------


@router.get("/status")
async def status(library: LibraryService = Depends(get_library)) -> IndexStatus:
    """Where the index stands, source by source: where to look when
    something seems to be missing."""
    return IndexStatus.of(library)


@router.get("/issues")
async def issues(
    severity: str | None = Query(None, pattern="^(error|warning)$"),
    limit: int = Query(200, ge=1, le=MAX_ISSUES_PER_PAGE),
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> IssueList:
    """Everything in the library that does not line up, each with the parts
    it is about: what to open to put it right."""
    return IssueList.of(
        library.snapshot,
        severity,
        limit,
        may_edit=lambda part: library.writable and policy.may_edit(actor, part),
    )


# -- parts ------------------------------------------------------------------


@router.get("/parts")
async def list_parts(
    q: str = Query("", max_length=200),
    status: str | None = Query(None, pattern=_STATUS),
    category: str | None = Query(None, max_length=64),
    where: list[str] = Query(default_factory=list),
    limit: int = Query(100, ge=1, le=MAX_PARTS_PER_PAGE),
    library: LibraryService = Depends(get_library),
) -> PartList:
    """The parts that match, and -- within a category -- what they can be
    narrowed by next.

    `where` is a condition on what a part says about itself, and may be
    given several times: `Resistance=10k`, `Voltage>=25V`, `Power<=0.25W`.
    Two that say what one parameter *is* are a choice between them;
    everything else has to hold together.
    """
    found = search.find(library.snapshot, q, status=status, category=category, where=where)
    return PartList.of(library, found.parts, limit, found.parameters)


@router.get("/parts/{ipn}")
async def get_part(
    part: Part = Depends(require_part), snapshot: Snapshot = Depends(get_snapshot)
) -> PartDetail:
    return PartDetail.of(part, snapshot)


@router.get("/parts/{ipn}/assets")
async def part_assets(
    part: Part = Depends(require_part),
    snapshot: Snapshot = Depends(get_snapshot),
    settings: Settings = Depends(get_settings),
) -> AssetBundleOut:
    """Everything KiCad needs to place this part, in the order to send it.

    A part that cannot be placed whole is refused: `PlacementError`, which
    beats placing a part with no body. Its message names library assets, not
    server paths, and is what the user can act on.
    """
    return AssetBundleOut.of(asset_bundle(part, snapshot, settings.placement))


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
        raise NotFound(f"{part.ipn} has no drawable symbol")
    return _svg(markup)


@router.get("/parts/{ipn}/footprint.svg")
async def part_footprint(
    part: Part = Depends(require_part), snapshot: Snapshot = Depends(get_snapshot)
) -> Response:
    markup = previews.footprint_svg(part, snapshot)
    if markup is None:
        raise NotFound(f"{part.ipn} has no drawable footprint")
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
        raise NotFound(f"{part.ipn} has no 3D model")
    return FileResponse(
        path,
        media_type="model/step",
        filename=path.name,
        headers={"Cache-Control": "private, max-age=3600"},
    )

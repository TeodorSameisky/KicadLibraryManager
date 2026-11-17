"""Bills of materials: uploading them, reading them, and where a part is used.

These routes are plain functions rather than coroutines. They talk to SQLite,
which blocks, so they are run on the server's worker threads instead of on the
event loop that every other request is waiting on.

What uploading, attaching and deleting take is `app.services.boms`. If the
database cannot be used, the routes that answer in JSON say so through
`app.web.errors`; the pages show what they can without it.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from app.boms.report import BomReport, build_report
from app.boms.store import Bom, BomExists, BomStore, StoreUnavailable
from app.domain.parts import Part
from app.domain.policy import Actor
from app.library.snapshot import Snapshot
from app.services import boms as service
from app.services import costing
from app.services.supply import Supply
from app.web.deps import (
    get_actor,
    get_boms,
    get_snapshot,
    get_supply,
    require_access,
    require_part,
)
from app.web.schemas.boms import (
    BomAttached,
    BomConflict,
    BomCost,
    BomDetail,
    BomList,
    BomSummary,
    BomUploaded,
    DeviceChoice,
    Upload,
    WhereUsed,
)
from app.web.templating import render

api = APIRouter(prefix="/api/v1", tags=["boms"])
pages = APIRouter(include_in_schema=False)


def _require_bom(bom_id: int, boms: BomStore = Depends(get_boms)) -> Bom:
    return service.get_bom(boms, bom_id)


def _require_report(
    bom: Bom = Depends(_require_bom), snapshot: Snapshot = Depends(get_snapshot)
) -> BomReport:
    return build_report(bom, snapshot)


# -- as JSON ----------------------------------------------------------------


@api.get("/boms")
def list_boms(boms: BomStore = Depends(get_boms)) -> BomList:
    return BomList(boms=[BomSummary.of(bom) for bom in boms.list()])


@api.post(
    "/boms", status_code=201, response_model=BomUploaded, responses={409: {"model": BomConflict}}
)
def upload_bom(
    upload: Upload,
    boms: BomStore = Depends(get_boms),
    snapshot: Snapshot = Depends(get_snapshot),
    actor: Actor = Depends(get_actor),
) -> BomUploaded | JSONResponse:
    """Read a BOM exported from KiCad and keep it. One per project and revision."""
    try:
        bom, warnings = service.upload_bom(boms, snapshot, upload.asked(), actor=actor)
    except BomExists as exc:
        # Answered here and not with the rest: the page asks whether to
        # replace it, and needs to say what it would be replacing.
        conflict = BomConflict(
            detail=f"A BOM for {exc.existing.label} is already stored.",
            existing=BomSummary.of(exc.existing),
        )
        return JSONResponse(status_code=409, content=conflict.model_dump())
    return BomUploaded.of_upload(bom, warnings)


@api.put("/boms/{bom_id}/device")
def attach_bom(
    choice: DeviceChoice,
    bom: Bom = Depends(_require_bom),
    boms: BomStore = Depends(get_boms),
    snapshot: Snapshot = Depends(get_snapshot),
) -> BomAttached:
    """Say which device a stored BOM is the BOM of, or take it off one."""
    return BomAttached(id=bom.id, device=service.attach_bom(boms, snapshot, bom, choice.device))


@api.get("/boms/{bom_id}")
def get_bom(report: BomReport = Depends(_require_report)) -> BomDetail:
    """A BOM with every line looked up in the library as it is now."""
    return BomDetail.of_report(report)


def _attachment_name(bom: Bom, suffix: str) -> str:
    """A file name that is safe in a header, whatever the project is called."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", "-".join(filter(None, (bom.project, bom.revision))))
    return f"{stem.strip('_') or 'bom'}{suffix}"


def _csv(text: str, filename: str) -> Response:
    return Response(
        # With a byte-order mark: without one Excel reads UTF-8 as its local
        # code page and turns every µ and Ω into two wrong characters.
        content="﻿" + text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api.get("/boms/{bom_id}/export.csv")
def export_bom(report: BomReport = Depends(_require_report)) -> Response:
    """The BOM with the library's columns added: what purchasing needs."""
    return _csv(report.csv(), _attachment_name(report.bom, "-bom.csv"))


@api.get("/boms/{bom_id}/source.csv")
def bom_source(bom: Bom = Depends(_require_bom), boms: BomStore = Depends(get_boms)) -> Response:
    """The file exactly as it was uploaded."""
    return _csv(boms.source(bom.id) or "", _attachment_name(bom, "-as-uploaded.csv"))


# -- what it costs: worked out from what is kept, asked for on request

_BOARDS = Query(1, ge=1, le=costing.MAX_BOARDS, description="How many boards are built")


@api.get("/boms/{bom_id}/cost")
def bom_cost(
    boards: int = _BOARDS,
    bom: Bom = Depends(_require_bom),
    snapshot: Snapshot = Depends(get_snapshot),
    supply: Supply = Depends(get_supply),
) -> BomCost:
    """What building `boards` boards costs, from what the suppliers have
    already said. Asks none of them: `unasked` says how much is not known."""
    return BomCost.of(costing.cost_bom(bom, snapshot, supply, boards))


@api.post("/boms/{bom_id}/cost/ask")
def ask_bom_cost(
    boards: int = _BOARDS,
    bom: Bom = Depends(_require_bom),
    snapshot: Snapshot = Depends(get_snapshot),
    supply: Supply = Depends(get_supply),
) -> BomCost:
    """Ask the suppliers about the BOM's manufacturer parts that nothing is
    known of, for a few seconds, and say what the cost now comes to. Asked
    again while `unasked` goes down, it gets through a BOM of any size
    without asking a supplier more often than it allows."""
    costing.ask_for_bom(bom, snapshot, supply)
    return BomCost.of(costing.cost_bom(bom, snapshot, supply, boards))


@api.get("/boms/{bom_id}/cost.csv")
def export_bom_cost(
    boards: int = _BOARDS,
    bom: Bom = Depends(_require_bom),
    snapshot: Snapshot = Depends(get_snapshot),
    supply: Supply = Depends(get_supply),
) -> Response:
    """The costing, line by line: where each is bought and what it comes to."""
    worked_out = costing.cost_bom(bom, snapshot, supply, boards)
    return _csv(worked_out.csv(), _attachment_name(bom, f"-cost-{worked_out.boards}.csv"))


@api.delete("/boms/{bom_id}", status_code=204)
def delete_bom(
    bom_id: int, boms: BomStore = Depends(get_boms), actor: Actor = Depends(get_actor)
) -> Response:
    """Throw a record away: a librarian's to do, since it cannot be undone."""
    service.delete_bom(boms, bom_id, actor=actor)
    return Response(status_code=204)


@api.get("/parts/{ipn}/where-used")
def where_used(part: Part = Depends(require_part), boms: BomStore = Depends(get_boms)) -> WhereUsed:
    """Every stored BOM this part is on."""
    return WhereUsed.of(part, boms.where_used(part.ipn))


# -- as pages ---------------------------------------------------------------


@pages.get("/boms")
def boms_page(
    request: Request,
    boms: BomStore = Depends(get_boms),
    snapshot: Snapshot = Depends(get_snapshot),
) -> Response:
    try:
        stored, problem = boms.list(), None
    except StoreUnavailable as exc:
        stored, problem = [], str(exc)
    return render(request, "boms.html", boms=stored, problem=problem, products=snapshot.products)


@pages.get("/boms/{bom_id}")
def bom_page(
    request: Request,
    report: BomReport = Depends(_require_report),
    snapshot: Snapshot = Depends(get_snapshot),
) -> Response:
    products = snapshot.products
    return render(
        request,
        "bom.html",
        bom=report.bom,
        report=report,
        products=products,
        device=products.device(report.bom.device),
        family=products.family(report.bom.device.partition("/")[0]),
    )


@pages.get("/boms/{bom_id}/cost")
def bom_cost_page(
    request: Request,
    # Text and not a number: it comes from a form, and something typed that
    # is not a number is one board, not an error page.
    boards: str = Query("1", max_length=12),
    bom: Bom = Depends(_require_bom),
    snapshot: Snapshot = Depends(get_snapshot),
    supply: Supply = Depends(get_supply),
) -> Response:
    return render(
        request,
        "bom_cost.html",
        bom=bom,
        costing=costing.cost_bom(bom, snapshot, supply, costing.boards_of([boards])),
        max_boards=costing.MAX_BOARDS,
    )


# Everything here reads or writes what the library's users have built, so one
# guard covers the lot.
router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)

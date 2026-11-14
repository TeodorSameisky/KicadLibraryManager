"""Bills of materials: uploading them, reading them, and where a part is used.

These routes are plain functions rather than coroutines. They talk to SQLite,
which blocks, so they are run on the server's worker threads instead of on the
event loop that every other request is waiting on.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.boms.parse import BomError, parse_bom
from app.boms.report import BomReport, build_report
from app.boms.store import Bom, BomExists, BomStore, StoreUnavailable, Usage
from app.library.parts import Part
from app.library.snapshot import Snapshot
from app.web.deps import get_boms, get_snapshot, require_access, require_part
from app.web.templating import render

# A BOM of ten thousand lines is under a megabyte.
MAX_BOM_CHARACTERS = 4 * 1024 * 1024

api = APIRouter(prefix="/api/v1", tags=["boms"])
pages = APIRouter(include_in_schema=False)


def _unavailable(exc: StoreUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail="BOMs cannot be stored or read right now.")


def _require_bom(bom_id: int, boms: BomStore = Depends(get_boms)) -> Bom:
    try:
        bom = boms.get(bom_id)
    except StoreUnavailable as exc:
        raise _unavailable(exc) from exc
    if bom is None:
        raise HTTPException(status_code=404, detail="There is no such BOM")
    return bom


def _require_report(
    bom: Bom = Depends(_require_bom), snapshot: Snapshot = Depends(get_snapshot)
) -> BomReport:
    return build_report(bom, snapshot)


# -- as JSON ----------------------------------------------------------------


def _summary(bom: Bom) -> dict[str, Any]:
    return {
        "id": bom.id,
        "project": bom.project,
        "revision": bom.revision,
        "filename": bom.filename,
        "note": bom.note,
        "uploaded_at": bom.uploaded_at,
        "uploaded_by": bom.uploaded_by,
        "lines": bom.line_count,
        "components": bom.component_count,
    }


def _usage(usage: Usage) -> dict[str, Any]:
    return {
        "bom": _summary(usage.bom),
        "quantity": usage.quantity,
        "references": list(usage.references),
        "dnp": usage.dnp,
    }


class Upload(BaseModel):
    """A BOM exported from KiCad, as text.

    Sent as JSON rather than as a form upload, so that a script -- or a KiCad
    plugin, one day -- can post one as easily as the page does.
    """

    project: str = Field(min_length=1, max_length=200)
    revision: str = Field("", max_length=50)
    note: str = Field("", max_length=2000)
    filename: str = Field("", max_length=255)
    content: str = Field(min_length=1, max_length=MAX_BOM_CHARACTERS)
    # Uploading a project's revision a second time replaces the first only
    # when this is set: overwriting a record should be chosen, not stumbled into.
    replace: bool = False


@api.get("/boms")
def list_boms(boms: BomStore = Depends(get_boms)) -> dict[str, Any]:
    try:
        return {"boms": [_summary(bom) for bom in boms.list()]}
    except StoreUnavailable as exc:
        raise _unavailable(exc) from exc


@api.post("/boms", status_code=201)
def upload_bom(
    upload: Upload,
    boms: BomStore = Depends(get_boms),
    session: Session | None = Depends(require_access),
) -> Any:
    project, revision = " ".join(upload.project.split()), " ".join(upload.revision.split())
    if not project:
        raise HTTPException(status_code=422, detail="The project needs a name.")

    try:
        parsed = parse_bom(upload.content)
    except BomError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        bom = boms.save(
            parsed,
            project=project,
            revision=revision,
            filename=upload.filename,
            note=upload.note.strip(),
            uploaded_by=session.display_name if session else "",
            source=upload.content,
            replace_existing=upload.replace,
        )
    except BomExists as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"A BOM for {exc.existing.label} is already stored.",
                "existing": _summary(exc.existing),
            },
        )
    except StoreUnavailable as exc:
        raise _unavailable(exc) from exc

    return {**_summary(bom), "warnings": list(parsed.warnings)}


@api.get("/boms/{bom_id}")
def get_bom(report: BomReport = Depends(_require_report)) -> dict[str, Any]:
    """A BOM with every line looked up in the library as it is now."""
    return {
        **_summary(report.bom),
        "parts": report.part_count,
        "problems": report.problems,
        "cautions": report.cautions,
        "items": [
            {
                "references": list(row.line.references),
                "quantity": row.line.quantity,
                "dnp": row.line.dnp,
                "ipn": row.line.ipn,
                "value": row.line.value,
                "footprint": row.line.footprint,
                "known": row.part is not None,
                "description": row.description,
                "status": row.part.status if row.part else None,
                "mpn": row.preferred_mpn or None,
                "manufacturer": row.source.manufacturer if row.source else None,
                "lifecycle": row.source.lifecycle if row.source else None,
                "alternates": list(row.alternates),
                "remarks": [{"level": r.level, "text": r.text} for r in row.remarks],
            }
            for row in report.lines
        ],
    }


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


@api.delete("/boms/{bom_id}", status_code=204)
def delete_bom(bom: Bom = Depends(_require_bom), boms: BomStore = Depends(get_boms)) -> Response:
    try:
        boms.delete(bom.id)
    except StoreUnavailable as exc:
        raise _unavailable(exc) from exc
    return Response(status_code=204)


@api.get("/parts/{ipn}/where-used")
def where_used(
    part: Part = Depends(require_part), boms: BomStore = Depends(get_boms)
) -> dict[str, Any]:
    """Every stored BOM this part is on."""
    try:
        return {"ipn": part.ipn, "used_in": [_usage(usage) for usage in boms.where_used(part.ipn)]}
    except StoreUnavailable as exc:
        raise _unavailable(exc) from exc


# -- as pages ---------------------------------------------------------------


@pages.get("/boms")
def boms_page(request: Request, boms: BomStore = Depends(get_boms)) -> Response:
    try:
        stored, problem = boms.list(), None
    except StoreUnavailable as exc:
        stored, problem = [], str(exc)
    return render(request, "boms.html", boms=stored, problem=problem)


@pages.get("/boms/{bom_id}")
def bom_page(request: Request, report: BomReport = Depends(_require_report)) -> Response:
    return render(request, "bom.html", bom=report.bom, report=report)


# Everything here reads or writes what the library's users have built, so one
# guard covers the lot.
router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)

"""Requesting a part, correcting it, and approving it: over HTTP.

What may be done, by whom, and what it takes is `app.services.parts`. These
routes turn a request into a call to it and the outcome into an answer; what
goes wrong on the way is answered by `app.web.errors`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.domain.parts import Part, PartStatus
from app.domain.policy import Actor
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import parts
from app.web.deps import get_actor, get_library, get_snapshot, require_access, require_part
from app.web.schemas.library import Choices, PartBody, PartRef, StatusBody
from app.web.templating import render

api = APIRouter(prefix="/api/v1", tags=["editing"])
pages = APIRouter(include_in_schema=False)


# -- as JSON ----------------------------------------------------------------


@api.get("/library/choices")
async def choices(
    snapshot: Snapshot = Depends(get_snapshot), library: LibraryService = Depends(get_library)
) -> Choices:
    """What a part can be made from: for the form's lists."""
    return Choices.of(snapshot, writable=library.writable)


@api.post("/parts", status_code=201)
async def request_part(
    body: PartBody,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> PartRef:
    """Add a part as a draft, under the next number in its category."""
    ipn = await parts.request_part(library, body.form(), note=body.note, actor=actor)
    return PartRef(ipn=ipn, status=PartStatus.DRAFT.value)


@api.put("/parts/{ipn}")
async def edit_part(
    body: PartBody,
    part: Part = Depends(require_part),
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> PartRef:
    """Change a part. A draft is anyone's to correct; the rest is a librarian's."""
    await parts.edit_part(library, part, body.form(), note=body.note, actor=actor)
    return PartRef(ipn=part.ipn, status=part.status)


@api.post("/parts/{ipn}/status")
async def set_status(
    body: StatusBody,
    part: Part = Depends(require_part),
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> PartRef:
    """Approve a part, retire it, or send it back to being a draft."""
    await parts.set_status(library, part, body.status, note=body.note, actor=actor)
    return PartRef(ipn=part.ipn, status=body.status)


# -- as pages ---------------------------------------------------------------


def _form_data(part: Part | None) -> dict[str, Any]:
    """What the form starts out holding."""
    if part is None:
        return {"ipn": None, "fields": {}, "sources": []}
    return {
        "ipn": part.ipn,
        "category": part.category,
        "description": part.description,
        "symbol": part.symbol,
        "footprint": part.footprint,
        "fields": dict(part.fields),
        "sources": [{"mpn": ref.mpn, "preferred": ref.preferred} for ref in part.mpns],
    }


@pages.get("/parts/new")
async def new_part_page(
    request: Request, library: LibraryService = Depends(get_library)
) -> Response:
    return render(
        request, "part_form.html", part=None, data=_form_data(None), writable=library.writable
    )


@pages.get("/ipn/{ipn}/edit")
async def edit_part_page(
    request: Request,
    part: Part = Depends(require_part),
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> Response:
    parts.require_may_edit(actor, part)
    return render(
        request, "part_form.html", part=part, data=_form_data(part), writable=library.writable
    )


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)

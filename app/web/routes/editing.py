"""Requesting a part, correcting it, and approving it.

Anyone signed in may request a part: it is added as a draft under the next
number in its category. A draft may be corrected by anyone. Once a librarian
has approved it, only a librarian changes it.

Every one of these ends in a commit to the library's repository, made in the
name of whoever asked.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth.roles import is_librarian
from app.auth.sessions import Session
from app.config import Settings
from app.library import editing
from app.library.editing import EditError, PartForm, SourceEntry
from app.library.parts import PART_STATUSES, Part
from app.library.publish import Identity, PublishError
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.web.deps import (
    get_library,
    get_settings,
    get_snapshot,
    require_access,
    require_librarian,
    require_part,
)
from app.web.templating import render

log = logging.getLogger(__name__)

api = APIRouter(prefix="/api/v1", tags=["editing"])
pages = APIRouter(include_in_schema=False)

_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+$")


# -- who is asking ----------------------------------------------------------


def _author(session: Session | None, settings: Settings) -> Identity:
    """Who a commit is made in the name of.

    Their own e-mail address if the identity provider gave one. Otherwise an
    address that names them and plainly cannot receive mail, which is what
    git hosts do for the same reason.
    """
    if session is None:
        return Identity("Someone", f"nobody@users.noreply.{settings.hostname}")

    # A name goes into a commit header; keep what would break one out of it.
    name = re.sub(r"[<>\r\n]", "", session.display_name).strip() or "Someone"
    email = str(session.principal.claims.get("email") or "")
    if not _EMAIL.match(email):
        known_as = str(session.principal.claims.get("preferred_username") or session.subject[:12])
        local = re.sub(r"[^A-Za-z0-9._-]", "-", known_as) or "user"
        email = f"{local}@users.noreply.{settings.hostname}"
    return Identity(name, email)


def _may_edit(part: Part, session: Session | None, settings: Settings) -> bool:
    """A draft is still being worked out, by whoever is working it out.
    What a librarian has approved is the librarians' to change."""
    if part.status == "draft":
        return True
    principal = session.principal if session else None
    return is_librarian(principal, settings.librarians, auth_configured=settings.auth_configured)


def _require_writable(library: LibraryService = Depends(get_library)) -> LibraryService:
    if not library.writable:
        raise HTTPException(
            status_code=503, detail="This deployment is not set up to change the library."
        )
    return library


# -- what is asked for ------------------------------------------------------


class SourceBody(BaseModel):
    mpn: str = Field(min_length=1, max_length=120)
    preferred: bool = False
    manufacturer: str = Field("", max_length=120)
    datasheet: str = Field("", max_length=500)
    lifecycle: str = Field("active", max_length=20)


class PartBody(BaseModel):
    category: str = Field("", max_length=32)
    description: str = Field(max_length=300)
    symbol: str = Field(max_length=200)
    footprint: str = Field("", max_length=200)
    fields: dict[str, str] = Field(default_factory=dict)
    sources: list[SourceBody] = Field(default_factory=list)

    def form(self) -> PartForm:
        return PartForm(
            category=self.category,
            description=self.description,
            symbol=self.symbol.strip(),
            footprint=self.footprint.strip(),
            fields=dict(self.fields),
            sources=tuple(
                SourceEntry(
                    mpn=source.mpn.strip(),
                    preferred=source.preferred,
                    manufacturer=source.manufacturer,
                    datasheet=source.datasheet.strip(),
                    lifecycle=source.lifecycle,
                )
                for source in self.sources
            ),
        )


class StatusBody(BaseModel):
    status: str = Field(pattern=f"^({'|'.join(PART_STATUSES)})$")


def _refused(error: EditError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(error), "problems": error.problems})


async def _publish(library: LibraryService, write: Any, author: Identity) -> JSONResponse | None:
    """Make a change, turning what can go wrong into an answer.

    Returns None when it worked.
    """
    try:
        await library.change(write, author)
    except EditError as exc:
        return _refused(exc)
    except PublishError as exc:
        log.warning("a change to the library could not be published: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": f"The change could not be saved to the repository: {exc}"},
        )
    return None


# -- as JSON ----------------------------------------------------------------


@api.get("/library/choices")
async def choices(
    snapshot: Snapshot = Depends(get_snapshot), library: LibraryService = Depends(get_library)
) -> dict[str, Any]:
    """What a part can be made from: for the form's lists."""
    catalog = snapshot.catalog
    return {
        "writable": library.writable,
        "categories": [
            {"code": category.code, "name": category.name}
            for category in sorted(snapshot.parts.categories.values(), key=lambda c: c.code)
        ],
        "symbols": sorted(
            {f"{lib}:{name}" for index in catalog.indexes.values() for lib, name in index.symbols}
        ),
        "footprints": sorted(
            {
                f"{lib}:{name}"
                for index in catalog.indexes.values()
                for lib, name in index.footprints
            }
        ),
        "mpns": [
            {"mpn": mpn.mpn, "manufacturer": mpn.manufacturer, "lifecycle": mpn.lifecycle}
            for mpn in sorted(snapshot.parts.mpns.values(), key=lambda m: m.mpn)
        ],
    }


@api.post("/parts", status_code=201)
async def request_part(
    body: PartBody,
    settings: Settings = Depends(get_settings),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(_require_writable),
    session: Session | None = Depends(require_access),
) -> Any:
    """Add a part as a draft, under the next number in its category."""
    form = body.form()
    try:
        editing.check(form, snapshot, new=True)
    except EditError as exc:
        return _refused(exc)

    given: dict[str, str] = {}

    def write(root):
        given["ipn"], message = editing.write_new_part(root, form)
        return message

    failed = await _publish(library, write, _author(session, settings))
    return failed or {"ipn": given["ipn"], "status": "draft"}


@api.put("/parts/{ipn}")
async def edit_part(
    body: PartBody,
    part: Part = Depends(require_part),
    settings: Settings = Depends(get_settings),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(_require_writable),
    session: Session | None = Depends(require_access),
) -> Any:
    if not _may_edit(part, session, settings):
        raise HTTPException(
            status_code=403, detail="Only a librarian can change a part that is not a draft."
        )

    form = body.form()
    try:
        editing.check(form, snapshot, new=False)
    except EditError as exc:
        return _refused(exc)

    failed = await _publish(
        library, lambda root: editing.write_part(root, part.ipn, form), _author(session, settings)
    )
    return failed or {"ipn": part.ipn, "status": part.status}


@api.post("/parts/{ipn}/status")
async def set_status(
    body: StatusBody,
    part: Part = Depends(require_part),
    settings: Settings = Depends(get_settings),
    library: LibraryService = Depends(_require_writable),
    session: Session | None = Depends(require_librarian),
) -> Any:
    """Approve a part, retire it, or send it back to being a draft."""
    failed = await _publish(
        library,
        lambda root: editing.write_status(root, part.ipn, body.status),
        _author(session, settings),
    )
    return failed or {"ipn": part.ipn, "status": body.status}


# -- as pages ---------------------------------------------------------------


def _form_data(part: Part | None, snapshot: Snapshot) -> dict[str, Any]:
    """What the form starts out holding."""
    if part is None:
        return {"ipn": None, "fields": {"Value": ""}, "sources": []}
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
    request: Request,
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
) -> Response:
    return render(
        request, "part_form.html", part=None, data=_form_data(None, snapshot),
        writable=library.writable,
    )  # fmt: skip


@pages.get("/ipn/{ipn}/edit")
async def edit_part_page(
    request: Request,
    part: Part = Depends(require_part),
    settings: Settings = Depends(get_settings),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
    session: Session | None = Depends(require_access),
) -> Response:
    if not _may_edit(part, session, settings):
        raise HTTPException(
            status_code=403, detail="Only a librarian can change a part that is not a draft."
        )
    return render(
        request, "part_form.html", part=part, data=_form_data(part, snapshot),
        writable=library.writable,
    )  # fmt: skip


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)

"""Keeping the list of manufacturers: over HTTP.

What may be done, by whom, and what it takes is `app.services.manufacturers`.
Reading the list is anyone's who may read the library; changing it, and the
pages for changing it, are a librarian's.

A manufacturer is named in the body of a request and not in its address: a
name may hold a slash, and an address would take it apart.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.domain.manufacturers import name_key
from app.domain.policy import Actor
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import manufacturers
from app.web.deps import get_actor, get_library, get_snapshot, require_access, require_librarian
from app.web.schemas.library import ManufacturerBody, ManufacturerList, ManufacturerRemoval
from app.web.templating import render

api = APIRouter(prefix="/api/v1/manufacturers", tags=["manufacturers"])
pages = APIRouter(include_in_schema=False, dependencies=[Depends(require_librarian)])


# -- as JSON ----------------------------------------------------------------


@api.get("")
async def list_manufacturers(
    snapshot: Snapshot = Depends(get_snapshot), library: LibraryService = Depends(get_library)
) -> ManufacturerList:
    """The manufacturers the library knows, and the names manufacturer parts
    give that it does not."""
    return ManufacturerList.of(manufacturers.overview(snapshot), writable=library.writable)


@api.put("")
async def save_manufacturer(
    body: ManufacturerBody,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> ManufacturerList:
    """Add a manufacturer to the list, or change the one called `was`."""
    after = await manufacturers.save_manufacturer(
        library, body.form(), was=body.was, note=body.note, actor=actor
    )
    return ManufacturerList.of(after, writable=library.writable)


@api.delete("")
async def remove_manufacturer(
    body: ManufacturerRemoval,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> ManufacturerList:
    """Take a manufacturer off the list."""
    after = await manufacturers.remove_manufacturer(library, body.name, note=body.note, actor=actor)
    return ManufacturerList.of(after, writable=library.writable)


# -- as pages ---------------------------------------------------------------


@pages.get("/manufacturers")
async def manufacturers_page(
    request: Request,
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
) -> Response:
    return render(
        request,
        "manufacturers.html",
        overview=manufacturers.overview(snapshot),
        writable=library.writable,
    )


@pages.get("/manufacturers/new")
async def new_manufacturer_page(
    request: Request,
    name: str = Query("", max_length=manufacturers.MAX_NAME),
    library: LibraryService = Depends(get_library),
) -> Response:
    """The form, empty -- or holding a name from the worklist, to be listed."""
    return render(
        request,
        "manufacturer_form.html",
        was=None,
        data={"was": None, "name": name.strip(), "aliases": []},
        writable=library.writable,
    )


@pages.get("/manufacturers/edit")
async def edit_manufacturer_page(
    request: Request,
    name: str = Query(max_length=manufacturers.MAX_NAME),
    alias: str = Query("", max_length=manufacturers.MAX_NAME),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
) -> Response:
    """The form, holding a manufacturer -- and, if the worklist sent one, a
    spelling to add to it. Nothing is added until the form is saved."""
    listed = manufacturers.find(snapshot, name)
    aliases = list(listed.aliases)
    known = {name_key(spelling) for spelling in listed.spellings}
    if alias.strip() and name_key(alias) not in known:
        aliases.append(alias.strip())
    return render(
        request,
        "manufacturer_form.html",
        was=listed.name,
        data={"was": listed.name, "name": listed.name, "aliases": aliases},
        writable=library.writable,
    )


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)

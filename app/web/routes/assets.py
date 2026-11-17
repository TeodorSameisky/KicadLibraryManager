"""Symbols, footprints and 3D models: what the library has, and adding to it,
over HTTP.

What may be done, by whom, and what it takes is `app.services.assets`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.domain import policy
from app.domain.policy import Actor
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.library.uploads import AssetKind, own_index
from app.services import assets
from app.web.deps import get_actor, get_library, get_snapshot, require_access
from app.web.schemas.assets import AssetList, AssetRemoved, UploadBody, UploadOutcome
from app.web.schemas.library import NoteBody
from app.web.templating import render

api = APIRouter(prefix="/api/v1/assets", tags=["assets"])
pages = APIRouter(include_in_schema=False)

_KIND = Query(AssetKind.SYMBOL, description="symbol, footprint or model")


# -- as JSON ----------------------------------------------------------------


@api.get("")
async def list_assets(
    kind: AssetKind = _KIND,
    q: str = Query("", max_length=200, description="Words the name has to contain"),
    limit: int = Query(assets.LIST_LIMIT, ge=1, le=5000),
    snapshot: Snapshot = Depends(get_snapshot),
) -> AssetList:
    """The library's symbols, footprints or 3D models, each with what uses it."""
    return AssetList.of(assets.list_assets(snapshot, kind, q, limit))


@api.post("/check")
async def check_upload(
    body: UploadBody,
    snapshot: Snapshot = Depends(get_snapshot),
    actor: Actor = Depends(get_actor),
) -> UploadOutcome:
    """What an upload would add and replace, drawn, and what stands in its
    way. Changes nothing."""
    return UploadOutcome.of(assets.check_upload(snapshot, body.library, body.uploaded(), actor))


@api.post("", status_code=201)
async def upload(
    body: UploadBody,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> UploadOutcome:
    """Add symbols, footprints and 3D models to the library: one commit."""
    plan = await assets.upload(
        library,
        body.library,
        body.uploaded(),
        replace=body.replace,
        note=body.note,
        actor=actor,
    )
    return UploadOutcome.of(plan)


@api.delete("/{kind}/{ref:path}")
async def remove(
    kind: AssetKind,
    ref: str,
    body: NoteBody | None = None,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> AssetRemoved:
    """Take a symbol, footprint or 3D model that nothing uses out of the library."""
    await assets.remove(library, kind, ref, note=body.note if body else "", actor=actor)
    return AssetRemoved(kind=kind.value, ref=ref)


# -- as pages ---------------------------------------------------------------


def _libraries(snapshot: Snapshot) -> list[str]:
    """The libraries in the repository uploads go to, to offer by name."""
    owner = own_index(snapshot)
    if owner is None:
        return []
    index = owner[1]
    return sorted(
        {library for library, _ in index.symbols}
        | {library for library, _ in index.footprints}
        | {model.library for model in index.models.values()},
        key=str.casefold,
    )


@pages.get("/library")
async def library_page(
    request: Request,
    kind: str = Query("symbol"),
    q: str = Query("", max_length=200),
    snapshot: Snapshot = Depends(get_snapshot),
) -> Response:
    chosen = assets.require_kind(kind)
    counts = {
        AssetKind.SYMBOL: snapshot.catalog.symbol_count,
        AssetKind.FOOTPRINT: snapshot.catalog.footprint_count,
        AssetKind.MODEL: snapshot.catalog.model_count,
    }
    return render(
        request,
        "library.html",
        kind=chosen,
        kinds=list(AssetKind),
        counts=counts,
        query=q,
        listing=assets.list_assets(snapshot, chosen, q),
    )


@pages.get("/library/upload")
async def upload_page(
    request: Request,
    library: str = Query("", max_length=64),
    snapshot: Snapshot = Depends(get_snapshot),
) -> Response:
    return render(request, "library_upload.html", library=library, libraries=_libraries(snapshot))


@pages.get("/library/{kind}/{ref:path}")
async def asset_page(
    request: Request,
    kind: str,
    ref: str,
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> Response:
    detail = assets.asset_detail(snapshot, assets.require_kind(kind), ref)
    may_replace = library.writable and detail.own and policy.may_replace_assets(actor)
    unused = not detail.row.used_by and not detail.derived
    return render(
        request,
        "library_asset.html",
        detail=detail,
        may_replace=may_replace,
        may_remove=may_replace and unused,
    )


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)

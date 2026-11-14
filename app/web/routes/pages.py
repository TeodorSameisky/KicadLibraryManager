"""The pages: the panel KiCad embeds, a part's page, and two for maintainers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.auth.sessions import Session
from app.boms.store import BomStore, StoreUnavailable
from app.config import Settings
from app.library import previews
from app.library.parts import Part
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.web.deps import (
    current_session,
    get_boms,
    get_library,
    get_settings,
    get_snapshot,
    require_access,
    require_part,
)
from app.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["pages"], include_in_schema=False)


@router.get("/")
def status_page(
    request: Request,
    settings: Settings = Depends(get_settings),
    library: LibraryService = Depends(get_library),
) -> Response:
    """What an operator sees first: whether this deployment works.

    Open, because it is where a misconfigured sign-in gets diagnosed. It
    shows counts and configuration, and nothing out of the library itself.
    """
    snapshot = library.snapshot
    return render(
        request,
        "status.html",
        state=library.state.value,
        error=library.error,
        problems=settings.problems(),
        snapshot=snapshot,
        auth_configured=settings.auth_configured,
        web_login=settings.web_login_configured,
        callback_url=settings.web_callback_url,
        client_id=settings.oidc_client_id,
        boms_available=request.app.state.boms.problem() is None,
        checked_at=library.checked_at,
        refresh_minutes=settings.library_refresh_minutes,
        webhook=bool(settings.webhook_secret),
        metadata_url=f"{settings.public_url}/.well-known/kicad-remote-provider",
    )


@router.get("/panel")
async def panel(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: Session | None = Depends(current_session),
) -> Response:
    """The page KiCad loads in its Remote Symbols WebView.

    Open even when the library is not. This is the page with the sign-in
    button on it; refusing it would leave nowhere to sign in from. What it
    lists comes from the API, which is closed.
    """
    return render(
        request,
        "panel.html",
        auth_configured=settings.auth_configured,
        web_login=settings.web_login_configured,
        session=session,
        provider_version=settings.provider_version,
    )


@router.get("/ipn/{ipn}", dependencies=[Depends(require_access)])
def part_page(
    request: Request,
    part: Part = Depends(require_part),
    snapshot: Snapshot = Depends(get_snapshot),
    settings: Settings = Depends(get_settings),
    boms: BomStore = Depends(get_boms),
) -> Response:
    """A part's page: where the Datasheet field of every placed symbol leads."""
    try:
        usages = boms.where_used(part.ipn)
    except StoreUnavailable as exc:
        # The part is still worth showing without the list of where it is used.
        log.warning("where-used is unavailable for %s: %s", part.ipn, exc)
        usages = None

    return render(
        request,
        "part.html",
        part=part,
        category_name=snapshot.category_name(part.category),
        sources=[(ref, snapshot.mpn(ref.mpn)) for ref in part.mpns],
        related=snapshot.related(part),
        # Inlined rather than linked, so that pins and pads can be matched up.
        symbol_svg=previews.symbol_svg(part, snapshot, settings.placement),
        footprint_svg=previews.footprint_svg(part, snapshot),
        has_model=previews.model_path(part, snapshot) is not None,
        usages=usages,
    )


@router.get("/issues", dependencies=[Depends(require_access)])
async def issues_page(request: Request) -> Response:
    """Everything in the library that does not line up, as a working list."""
    return render(request, "issues.html")

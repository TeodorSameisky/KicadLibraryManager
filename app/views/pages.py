"""HTML pages: the panel KiCad embeds, and the part page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.api.dependencies import get_service, require_part
from app.api.previews import footprint_svg, symbol_svg
from app.auth.routes import current_session
from app.auth.session import Session
from app.config import Settings, get_settings
from app.library_service import LibraryService, State

router = APIRouter(tags=["pages"])


@router.get("/panel", response_class=HTMLResponse)
async def panel(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
    session: Session | None = Depends(current_session),
) -> HTMLResponse:
    """The page KiCad loads inside its Remote Symbols WebView."""
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="panel.html",
        context={
            "auth_configured": settings.auth_configured,
            "provider_name": settings.provider_name,
            "session": session,
            "root_path": settings.root_path,
            "indexing": service.state is State.SYNCING,
        },
    )


@router.get("/ipn/{ipn}", response_class=HTMLResponse)
async def ipn_page(
    ipn: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> HTMLResponse:
    """The page a placed symbol's Datasheet field points back to."""
    part = require_part(ipn, service)
    snapshot = service.snapshot
    parts_index = snapshot.parts

    mpns = [{"ref": ref, "detail": parts_index.mpns.get(ref.mpn)} for ref in part.mpns]

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="ipn.html",
        context={
            "part": part,
            "mpns": mpns,
            # Inlined rather than linked, so pins and pads can be linked.
            "symbol_svg": symbol_svg(part, snapshot, settings),
            "footprint_svg": footprint_svg(part, snapshot),
            "root_path": settings.root_path,
            "provider_name": settings.provider_name,
            "indexing": service.state is State.SYNCING,
        },
    )

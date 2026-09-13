"""HTML pages: the panel KiCad embeds, the part page, and the maintainer views."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import APP_VERSION
from app.api.dependencies import find_model, get_service, require_part
from app.auth.guard import require_access
from app.auth.routes import current_session
from app.auth.session import Session
from app.config import Settings, get_settings
from app.library_service import LibraryService, State
from app.previews import footprint_svg, symbol_svg

router = APIRouter(tags=["pages"])


async def not_signed_in(request: Request, exc: Exception) -> Response:
    """Answer a refused request in the shape its caller asked for.

    The same guard protects the JSON API and the pages a browser opens from a
    placed symbol's Datasheet link. A browser handed `{"detail": ...}` shows
    the user raw JSON and no way forward, so it gets a page with a sign-in
    route instead.
    """
    settings = get_settings()
    detail = getattr(exc, "detail", "Sign in to read this library")

    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse(status_code=401, content={"detail": detail})

    # Come back to the page that was refused, not to the panel: this link is
    # usually followed from a placed symbol and the part is the destination.
    here = request.url.path
    if request.url.query:
        here = f"{here}?{request.url.query}"

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="signed_out.html",
        status_code=401,
        context={
            "root_path": settings.root_path,
            "provider_name": settings.provider_name,
            "login_url": (
                f"{settings.root_path}/auth/login?next={quote(here, safe='')}"
                if settings.web_login_configured
                else None
            ),
        },
    )


def _render(request: Request, name: str, **context) -> HTMLResponse:
    settings = get_settings()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "root_path": settings.root_path,
            "provider_name": settings.provider_name,
            **context,
        },
    )


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
) -> HTMLResponse:
    """What an operator sees first: whether this deployment actually works.

    A list of links said nothing about the thing most likely to be wrong --
    that a source did not clone, or that the index is still running.
    """
    snapshot = service.snapshot
    return _render(
        request,
        "index.html",
        version=APP_VERSION,
        auth_configured=settings.auth_configured,
        web_login=settings.web_login_configured,
        callback_url=settings.web_callback_url,
        client_id=settings.oidc_client_id,
        state=service.state.value,
        error=service.error,
        sources=snapshot.sources,
        parts=snapshot.part_count(),
        symbols=snapshot.catalog.symbol_count(),
        errors=len(snapshot.errors),
        warnings=len(snapshot.warnings),
        indexed_at=snapshot.finished_at,
        problems=settings.problems(),
    )


@router.get("/panel", response_class=HTMLResponse)
async def panel(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: LibraryService = Depends(get_service),
    session: Session | None = Depends(current_session),
) -> HTMLResponse:
    """The page KiCad loads inside its Remote Symbols WebView.

    Public even when the library is not: this is the page that offers the
    sign-in button, so refusing it would leave the user nowhere to sign in
    from.
    """
    return _render(
        request,
        "panel.html",
        auth_configured=settings.auth_configured,
        web_login=settings.web_login_configured,
        session=session,
        indexing=service.state is State.SYNCING,
    )


@router.get("/issues", response_class=HTMLResponse, dependencies=[Depends(require_access)])
async def issues_page(
    request: Request,
    service: LibraryService = Depends(get_service),
) -> HTMLResponse:
    """Everything in the library that does not resolve.

    A real library always has some, and until now the only way to read them
    was to call the API by hand.
    """
    return _render(request, "issues.html", state=service.state.value)


@router.get("/ipn/{ipn}", response_class=HTMLResponse, dependencies=[Depends(require_access)])
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
    category = parts_index.categories.get(part.category)

    return _render(
        request,
        "ipn.html",
        part=part,
        mpns=mpns,
        category_name=category.name if category else part.category,
        # Every other IPN that lists one of this part's manufacturer parts --
        # the question asked when a manufacturer discontinues something.
        related=service.parts_sharing_mpn(part),
        # Inlined rather than linked, so pins and pads can be linked.
        symbol_svg=symbol_svg(part, snapshot, settings.public_url, settings.remote_library_prefix),
        footprint_svg=footprint_svg(part, snapshot),
        has_model=find_model(part, snapshot) is not None,
        indexing=service.state is State.SYNCING,
    )

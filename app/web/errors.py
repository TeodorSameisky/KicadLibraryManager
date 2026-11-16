"""Refusing a request in the shape its caller can use.

The same routes are reached by the panel's scripts, by KiCad and by a person
following a link. A script wants `{"detail": ...}`. A person handed that sees
raw JSON and no way forward, so a browser gets a page instead.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from app.errors import (
    AppError,
    Conflict,
    Invalid,
    NotAllowed,
    NotFound,
    Unavailable,
    UpstreamFailed,
)
from app.web.templating import render

log = logging.getLogger(__name__)

# What each kind of error is over HTTP. The one place that is decided: a
# route does not catch these to answer them, it lets them through to here.
_STATUS: tuple[tuple[type[AppError], int], ...] = (
    (NotFound, 404),
    (NotAllowed, 403),
    (Invalid, 422),
    (Conflict, 409),
    (Unavailable, 503),
    (UpstreamFailed, 502),
)

_TITLES = {
    400: "That request could not be used",
    401: "Sign in to continue",
    403: "Not allowed",
    404: "Not found",
    409: "Not possible right now",
    502: "The identity provider did not answer",
}


class NotSignedIn(HTTPException):
    """401 for a request that needs a session and has none."""

    def __init__(self) -> None:
        super().__init__(status_code=401, detail="Sign in to read this library")


def _wants_page(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


async def http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if not _wants_page(request):
        return await http_exception_handler(request, exc)

    settings = request.app.state.settings
    context = {
        "heading": _TITLES.get(exc.status_code, "Something went wrong"),
        "detail": exc.detail,
        "login_url": None,
        "signed_out": False,
    }

    if isinstance(exc, NotSignedIn):
        # Back to the page that was refused, not to the panel. This is usually
        # reached from a placed symbol's Datasheet link, and the part it
        # names is where the reader was going.
        here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        context["signed_out"] = True
        if settings.web_login_configured:
            context["login_url"] = f"{settings.root_path}/auth/login?next={quote(here, safe='')}"

    return render(request, "message.html", status_code=exc.status_code, **context)


async def app_error(request: Request, exc: Exception) -> Response:
    """Answer one of the app's own errors, whichever route it came out of."""
    assert isinstance(exc, AppError)
    status = next((code for kind, code in _STATUS if isinstance(exc, kind)), 500)

    if status >= 500:
        # The reason in full. The answer may be keeping it to itself: it can
        # name a path on the server.
        log.warning("%s %s could not be done: %s", request.method, request.url.path, exc)

    # Everything that is wrong, for a form to show next to its button.
    if isinstance(exc, Invalid) and exc.problems and not _wants_page(request):
        return JSONResponse(
            status_code=status, content={"detail": exc.detail(), "problems": list(exc.problems)}
        )
    return await http_error(request, HTTPException(status_code=status, detail=exc.detail()))

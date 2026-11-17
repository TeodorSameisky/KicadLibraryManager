"""Rendering pages."""

from __future__ import annotations

import time
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app import __version__
from app.auth.roles import actor_for
from app.boms.parse import compact_references
from app.domain import policy

_APP_DIR = Path(__file__).resolve().parent.parent

STATIC_DIR = _APP_DIR / "static"
templates = Jinja2Templates(directory=_APP_DIR / "templates")


class RevalidatedStaticFiles(StaticFiles):
    """Static files that a browser checks are current each time it uses them.

    With no instruction at all, a browser decides for itself how long to keep
    a script, and a script from before a deploy can be kept for hours after
    it, running against pages that have changed. `no-cache` does not mean
    "do not keep": the browser keeps the file but asks first, and the answer
    is a bodiless 304 while the file is unchanged -- the file's ETag is what
    it asks with. That costs a round trip for each file and nothing else, even
    for the 7 MB CAD kernel.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# A date, from the epoch seconds things are stamped with. In UTC and without a
# time: which day a BOM was uploaded is what gets asked, and a clock time
# would need a zone to mean anything.
templates.env.filters["day"] = lambda stamp: time.strftime("%Y-%m-%d", time.gmtime(stamp))
# For what happened minutes ago, where the day alone says nothing.
templates.env.filters["moment"] = lambda stamp: time.strftime(
    "%Y-%m-%d %H:%M UTC", time.gmtime(stamp)
)
templates.env.filters["references"] = compact_references


def money(amount: float, currency: str = "", exact: bool = False) -> str:
    """A price, with its currency after it: "1,234.50 EUR".

    To the cent; or, for what one piece costs, to as many as four places --
    a resistor costs a fraction of a cent, and 0.00 says nothing.
    """
    if exact:
        whole, _, places = f"{amount:,.4f}".partition(".")
        text = f"{whole}.{places.rstrip('0').ljust(2, '0')}"
    else:
        text = f"{amount:,.2f}"
    return f"{text} {currency}".strip()


templates.env.filters["money"] = money


def render(request: Request, template: str, *, status_code: int = 200, **context: Any) -> Response:
    """Render a template with what every page needs.

    `root_path` is the prefix a reverse proxy mounts the app under. Each link
    and asset address is written with it, because a bare "/static/..."
    resolves against the site root rather than against this app.
    """
    settings = request.app.state.settings
    session = request.app.state.sessions.get(request.cookies.get(settings.cookie_name))
    actor = actor_for(
        session.principal if session else None,
        settings.librarians,
        auth_configured=settings.auth_configured,
        hostname=settings.hostname,
    )
    return templates.TemplateResponse(
        request=request,
        name=template,
        status_code=status_code,
        context={
            "root_path": settings.root_path,
            "provider_name": settings.provider_name,
            "version": __version__,
            "writable": request.app.state.library.writable,
            # Decides which actions a page offers. What a route allows is
            # decided by the route; this only keeps a page from offering a
            # button that would be refused.
            "librarian": actor.librarian,
            # Asked by a page of a part, so that the page and the route that
            # would refuse it are told the same thing.
            "may_edit": partial(policy.may_edit, actor),
            **context,
        },
    )

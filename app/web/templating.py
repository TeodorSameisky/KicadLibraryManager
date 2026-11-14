"""Rendering pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app import __version__

_APP_DIR = Path(__file__).resolve().parent.parent

STATIC_DIR = _APP_DIR / "static"
templates = Jinja2Templates(directory=_APP_DIR / "templates")


def render(request: Request, template: str, *, status_code: int = 200, **context: Any) -> Response:
    """Render a template with what every page needs.

    `root_path` is the prefix a reverse proxy mounts the app under. Each link
    and asset address is written with it, because a bare "/static/..."
    resolves against the site root rather than against this app.
    """
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name=template,
        status_code=status_code,
        context={
            "root_path": settings.root_path,
            "provider_name": settings.provider_name,
            "version": __version__,
            **context,
        },
    )

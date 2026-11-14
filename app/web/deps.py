"""What a route can ask for.

Everything long-lived hangs off the application, put there by the factory, so
a route never reaches for a global and a test can build an app around its own
settings and library.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.auth.oidc import OidcClient
from app.auth.sessions import Session, SessionStore
from app.config import Settings
from app.library.parts import Part
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.web.errors import NotSignedIn


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


def get_identity(request: Request) -> OidcClient:
    return request.app.state.identity


def get_library(request: Request) -> LibraryService:
    return request.app.state.library


def get_snapshot(library: LibraryService = Depends(get_library)) -> Snapshot:
    """The index as it stands. Held for the whole request, so one response
    never mixes two of them."""
    return library.snapshot


def current_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    sessions: SessionStore = Depends(get_sessions),
) -> Session | None:
    return sessions.get(request.cookies.get(settings.cookie_name))


def require_access(
    settings: Settings = Depends(get_settings),
    session: Session | None = Depends(current_session),
) -> Session | None:
    """Let a request through to the library, or refuse it.

    Applied to whole routers rather than repeated on each route: a protected
    endpoint that was simply forgotten looks exactly like an open one until
    somebody goes looking.

    With authentication off this lets everyone through. A library served on a
    trusted network is a supported deployment, and the discovery document
    says so by advertising auth type "none".
    """
    if settings.auth_configured and session is None:
        raise NotSignedIn
    return session


def require_part(ipn: str, snapshot: Snapshot = Depends(get_snapshot)) -> Part:
    part = snapshot.part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"There is no part {ipn}")
    return part

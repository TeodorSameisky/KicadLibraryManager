"""What a route can ask for.

Everything long-lived hangs off the application, put there by the factory, so
a route never reaches for a global and a test can build an app around its own
settings and library.
"""

from __future__ import annotations

from typing import cast

from fastapi import Depends, Request

from app.auth.identity import Principal
from app.auth.oidc import OidcClient
from app.auth.roles import actor_for
from app.auth.sessions import Session, SessionStore
from app.auth.tokens import TokenStore
from app.boms.store import BomStore
from app.config import Settings
from app.db.backup import Backups
from app.domain.parts import Part
from app.domain.policy import Actor
from app.errors import NotAllowed, NotFound
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services.supply import Supply
from app.web.errors import NotSignedIn


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_sessions(request: Request) -> SessionStore:
    return cast(SessionStore, request.app.state.sessions)


def get_identity(request: Request) -> OidcClient:
    return cast(OidcClient, request.app.state.identity)


def get_library(request: Request) -> LibraryService:
    return cast(LibraryService, request.app.state.library)


def get_boms(request: Request) -> BomStore:
    return cast(BomStore, request.app.state.boms)


def get_backups(request: Request) -> Backups:
    return cast(Backups, request.app.state.backups)


def get_supply(request: Request) -> Supply:
    return cast(Supply, request.app.state.supply)


def get_tokens(request: Request) -> TokenStore:
    return cast(TokenStore, request.app.state.tokens)


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


def require_reader(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: Session | None = Depends(current_session),
    tokens: TokenStore = Depends(get_tokens),
) -> Principal | None:
    """Let a reader of the HTTP library through: KiCad, with a personal token.

    KiCad sends the token from the `.kicad_httplib` file as
    `Authorization: Token <token>`. A session is accepted as well, so the same
    answers can be looked at in a browser. With authentication off, as for
    everything else, everyone is let through.
    """
    if not settings.auth_configured:
        return None
    if session is not None:
        return session.principal
    scheme, _, secret = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() in ("token", "bearer"):
        principal = tokens.principal(secret.strip())
        if principal is not None:
            return principal
    raise NotSignedIn


def get_actor(
    settings: Settings = Depends(get_settings),
    session: Session | None = Depends(current_session),
) -> Actor:
    """Whoever is asking, as the rules about who may change what see them."""
    return actor_for(
        session.principal if session else None,
        settings.librarians,
        auth_configured=settings.auth_configured,
        hostname=settings.hostname,
    )


def require_librarian(
    session: Session | None = Depends(require_access),
    actor: Actor = Depends(get_actor),
) -> Session | None:
    """Let a librarian through. Asked after `require_access`, so someone who
    is not signed in is told to sign in, not that they lack a role."""
    if not actor.librarian:
        raise NotAllowed("Only a librarian can do this.")
    return session


def require_part(ipn: str, snapshot: Snapshot = Depends(get_snapshot)) -> Part:
    part = snapshot.part(ipn)
    if part is None:
        raise NotFound(f"There is no part {ipn}")
    return part

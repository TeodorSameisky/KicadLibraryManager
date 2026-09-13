"""Who is allowed to read the library.

The discovery document advertises an auth type and the panel offers a sign-in
button, so a deployment that sets AUTH_ENABLED expects the library to be
closed. Enforcement lives here, in one dependency, rather than being repeated
per route -- a protected endpoint that is simply forgotten looks identical to
an open one until someone goes looking.

The panel page itself stays public: it is what offers the sign-in button, and a
401 there would leave the user with nowhere to sign in from.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from app.auth.routes import current_session
from app.auth.session import Session
from app.config import Settings, get_settings


class NotSignedIn(HTTPException):
    """401 for a request that needs a session and has none.

    Its own type so the HTML pages can answer with a sign-in page while the
    API answers with JSON.
    """

    def __init__(self) -> None:
        super().__init__(status_code=401, detail="Sign in to read this library")


def require_access(
    settings: Settings = Depends(get_settings),
    session: Session | None = Depends(current_session),
) -> Session | None:
    """Allow the request through, or refuse it when auth is configured.

    With auth switched off this is deliberately a no-op rather than an error:
    a library served on a trusted network is a supported deployment, and the
    discovery document says so.
    """
    if not settings.auth_configured:
        return None
    if session is None:
        raise NotSignedIn
    return session

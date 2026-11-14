"""Signing in and out.

KiCad arrives already holding an access token:

    KiCad   --POST {access_token, next_url}-->  /api/v1/session/bootstrap
            <------------ {nonce_url} ---------
    WebView --GET-->  /session/consume?n=...    Set-Cookie, 303 to next_url

A browser arrives with nothing, and cannot borrow KiCad's session: the WebView
keeps its own cookie jar. So it signs in against the same provider, with this
app as the client:

    Browser --GET-->  /auth/login?next=...      307 to the provider
            --GET-->  /auth/callback?code=&state=
                                                Set-Cookie, 303 to next
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.oidc import (
    IdentityError,
    OidcClient,
    ProviderError,
    code_challenge,
    new_code_verifier,
)
from app.auth.redirects import is_internal, landing_url
from app.auth.sessions import Session, SessionStore
from app.config import Settings
from app.web.deps import current_session, get_identity, get_sessions, get_settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["session"])


def _signed_in(session_id: str, next_url: str, settings: Settings) -> Response:
    """Redirect onwards, carrying the new session as a cookie."""
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        key=settings.cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        # Lax, not Strict. Coming back from the provider is a cross-site
        # navigation, and Strict would drop the cookie on the very request
        # that sets it.
        samesite="lax",
        path="/",
    )
    return response


# -- KiCad's handshake ------------------------------------------------------


class BootstrapRequest(BaseModel):
    access_token: str = Field(min_length=1)
    next_url: str | None = None


class BootstrapResponse(BaseModel):
    nonce_url: str


@router.post("/api/v1/session/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    body: BootstrapRequest,
    settings: Settings = Depends(get_settings),
    sessions: SessionStore = Depends(get_sessions),
    identity: OidcClient = Depends(get_identity),
) -> BootstrapResponse:
    """Exchange KiCad's access token for an address that sets a session."""
    if not settings.auth_configured:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")

    try:
        principal = await identity.verify(body.access_token)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderError as exc:
        log.warning("cannot verify a token: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # KiCad checks the destination too, but an open redirect here would hand
    # the new session to whatever origin the caller named.
    next_url = body.next_url or settings.panel_url
    if not is_internal(next_url, settings.public_url):
        next_url = settings.panel_url

    nonce = sessions.mint_nonce(principal, next_url)
    return BootstrapResponse(nonce_url=f"{settings.public_url}/session/consume?n={nonce}")


@router.get("/session/consume")
async def consume(
    n: str = Query(max_length=512),
    settings: Settings = Depends(get_settings),
    sessions: SessionStore = Depends(get_sessions),
) -> Response:
    """Spend a nonce: the WebView leaves with a session cookie."""
    redeemed = sessions.redeem_nonce(n)
    if redeemed is None:
        raise HTTPException(status_code=400, detail="This sign-in link has expired or been used")
    session_id, next_url = redeemed
    return _signed_in(session_id, next_url, settings)


# -- a browser's own sign-in ------------------------------------------------


def _require_web_login(settings: Settings = Depends(get_settings)) -> None:
    if not settings.web_login_configured:
        raise HTTPException(status_code=404, detail="Browser sign-in is not enabled")


@router.get("/auth/login", dependencies=[Depends(_require_web_login)])
async def login(
    next: str | None = Query(None, max_length=2048),
    settings: Settings = Depends(get_settings),
    sessions: SessionStore = Depends(get_sessions),
    identity: OidcClient = Depends(get_identity),
) -> Response:
    """Send the browser to the provider to sign in."""
    verifier = new_code_verifier()
    state = sessions.begin_login(verifier, landing_url(next, settings.public_url))

    try:
        url = await identity.authorization_url(
            redirect_uri=settings.web_callback_url,
            state=state,
            challenge=code_challenge(verifier),
        )
    except ProviderError as exc:
        sessions.finish_login(state)  # nothing left that could come back for it
        log.warning("cannot start a browser sign-in: %s", exc)
        raise HTTPException(status_code=502, detail="The identity provider is unreachable") from exc

    return RedirectResponse(url=url, status_code=307)


@router.get("/auth/callback", dependencies=[Depends(_require_web_login)])
async def callback(
    state: str = Query(max_length=512),
    code: str | None = Query(None, max_length=4096),
    error: str | None = Query(None, max_length=256),
    settings: Settings = Depends(get_settings),
    sessions: SessionStore = Depends(get_sessions),
    identity: OidcClient = Depends(get_identity),
) -> Response:
    """The provider has sent the browser back: finish, and set a session."""
    # Spent before anything else is looked at. A state that outlived a failed
    # attempt could be replayed against a later one.
    pending = sessions.finish_login(state)
    if pending is None:
        raise HTTPException(status_code=400, detail="This sign-in has expired; start again")

    if error:
        # Declining consent is not a fault, and the provider's own code is
        # what tells the two apart.
        log.info("the provider refused a browser sign-in: %s", error)
        raise HTTPException(status_code=401, detail=f"Sign-in was refused ({error})")
    if not code:
        raise HTTPException(status_code=400, detail="The provider returned no authorization code")

    try:
        principal = await identity.exchange_code(
            code=code,
            verifier=pending.code_verifier,
            redirect_uri=settings.web_callback_url,
        )
    except IdentityError as exc:
        log.warning("a browser sign-in failed: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderError as exc:
        log.warning("a browser sign-in could not be completed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return _signed_in(sessions.create(principal), pending.next_url, settings)


# -- the session itself -----------------------------------------------------


@router.get("/api/v1/session/me")
async def me(session: Session | None = Depends(current_session)) -> dict[str, Any]:
    if session is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "subject": session.subject,
        "display_name": session.display_name,
    }


@router.post("/api/v1/session/logout", status_code=204)
async def logout(
    request: Request,
    settings: Settings = Depends(get_settings),
    sessions: SessionStore = Depends(get_sessions),
) -> Response:
    sessions.revoke(request.cookies.get(settings.cookie_name))
    response = Response(status_code=204)
    response.delete_cookie(settings.cookie_name, path="/")
    return response

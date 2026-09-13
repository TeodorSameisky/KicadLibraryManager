"""Session endpoints. There are two ways in, because there are two clients.

KiCad already holds an access token by the time it reaches us:

    KiCad  --POST {access_token, next_url}--> /api/v1/session/bootstrap
           <--------- {nonce_url} ----------
    WebView --GET--> /session/consume?n=...  -> Set-Cookie, 303 to next_url

A browser has nothing, and cannot borrow KiCad's: the WebView keeps its own
cookie jar, and the part page link inside a placed symbol may be opened from a
PDF export with no KiCad running at all. So the browser signs in against the
same provider with this app as the client:

    Browser --GET--> /auth/login?next=...    -> 307 to the provider
            --GET--> /auth/callback?code=&state=
                                             -> Set-Cookie, 303 to next
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.oidc import OidcVerifier, TokenError, code_challenge_for, make_code_verifier
from app.auth.session import Session, SessionStore
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

router = APIRouter()


class BootstrapRequest(BaseModel):
    access_token: str = Field(min_length=1)
    next_url: str | None = None


class BootstrapResponse(BaseModel):
    nonce_url: str


def _is_internal_url(candidate: str, public_url: str) -> bool:
    """True when candidate points inside our own deployment.

    Origin alone is not enough when the app is mounted under a path prefix:
    another app on the same host would share the origin but not the prefix.
    """
    left, right = urlsplit(candidate), urlsplit(public_url)
    if (left.scheme, left.hostname, left.port) != (right.scheme, right.hostname, right.port):
        return False

    prefix = right.path.rstrip("/")
    if not prefix:
        return True
    return left.path == prefix or left.path.startswith(prefix + "/")


def get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_verifier(request: Request) -> OidcVerifier:
    return request.app.state.oidc_verifier


def current_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
) -> Session | None:
    return store.get(request.cookies.get(settings.cookie_name))


@router.post("/api/v1/session/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    payload: BootstrapRequest,
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
    verifier: OidcVerifier = Depends(get_verifier),
) -> BootstrapResponse:
    if not settings.auth_configured:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")

    try:
        principal = await verifier.verify(payload.access_token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # Only ever redirect back into our own origin. KiCad checks this too, but an
    # open redirect here would leak the freshly minted session to any origin the
    # caller names.
    next_url = payload.next_url or settings.panel_url
    if not _is_internal_url(next_url, settings.public_url):
        next_url = settings.panel_url

    nonce = store.mint_nonce(principal.subject, principal.claims, next_url)
    return BootstrapResponse(nonce_url=f"{settings.public_url}/session/consume?n={nonce}")


def _redirect_with_session(session_id: str, next_url: str, settings: Settings) -> Response:
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        key=settings.cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        # Lax, not Strict: arriving from the provider's domain is a
        # cross-site navigation, and Strict would drop the cookie on exactly
        # the request that sets it.
        samesite="lax",
        path="/",
    )
    return response


@router.get("/session/consume")
async def consume(
    n: str,
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
) -> Response:
    redeemed = store.redeem_nonce(n)
    if redeemed is None:
        raise HTTPException(status_code=400, detail="Invalid or expired nonce")

    session_id, next_url = redeemed
    return _redirect_with_session(session_id, next_url, settings)


# -- the browser's own sign-in ---------------------------------------------


def _safe_next(candidate: str | None, settings: Settings) -> str:
    """Where to land after signing in, refusing anywhere but our own pages.

    A `next` that leaves this deployment would hand the freshly minted session
    to whoever named it.
    """
    if not candidate:
        return settings.panel_url

    # A bare path is the common case: the page that refused the request knows
    # its own path, not the origin a proxy presents it under.
    if candidate.startswith("/") and not candidate.startswith("//"):
        prefix = settings.root_path
        if prefix and not candidate.startswith(prefix + "/") and candidate != prefix:
            candidate = prefix + candidate
        return f"{settings.origin}{candidate}"

    return candidate if _is_internal_url(candidate, settings.public_url) else settings.panel_url


@router.get("/auth/login")
async def login(
    next: str | None = Query(None, max_length=2048),
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
    verifier: OidcVerifier = Depends(get_verifier),
) -> Response:
    """Start Authorization Code + PKCE, with this app as the client."""
    if not settings.web_login_configured:
        raise HTTPException(status_code=404, detail="Browser sign-in is not enabled")

    code_verifier = make_code_verifier()
    state = store.begin_login(code_verifier, _safe_next(next, settings))

    try:
        url = await verifier.authorization_url(
            redirect_uri=settings.web_callback_url,
            state=state,
            code_challenge=code_challenge_for(code_verifier),
        )
    except (TokenError, httpx.HTTPError) as exc:
        log.warning("cannot start browser sign-in: %s", exc)
        raise HTTPException(status_code=502, detail="The identity provider is unreachable") from exc

    return RedirectResponse(url=url, status_code=307)


@router.get("/auth/callback")
async def callback(
    request: Request,
    state: str = Query(max_length=512),
    code: str | None = Query(None, max_length=4096),
    error: str | None = Query(None, max_length=256),
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
    verifier: OidcVerifier = Depends(get_verifier),
) -> Response:
    """Finish the exchange and hand the browser a session."""
    if not settings.web_login_configured:
        raise HTTPException(status_code=404, detail="Browser sign-in is not enabled")

    # Consumed whatever happens: a state that survived a failed attempt could
    # be replayed against a later one.
    pending = store.finish_login(state)
    if pending is None:
        raise HTTPException(status_code=400, detail="This sign-in expired; start again")

    code_verifier, next_url = pending

    if error:
        # The user declining consent is not a server fault, and the provider's
        # own code is what says which of the two it was.
        log.info("browser sign-in refused by the provider: %s", error)
        raise HTTPException(status_code=401, detail=f"Sign-in was refused ({error})")
    if not code:
        raise HTTPException(status_code=400, detail="The provider returned no authorization code")

    try:
        principal = await verifier.exchange_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=settings.web_callback_url,
        )
    except (TokenError, httpx.HTTPError) as exc:
        log.warning("browser sign-in failed for %s: %s", request.client, exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return _redirect_with_session(
        store.issue(principal.subject, principal.claims), next_url, settings
    )


@router.get("/api/v1/session/me")
async def me(session: Session | None = Depends(current_session)) -> dict[str, object]:
    if session is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "subject": session.subject,
        "display_name": session.display_name,
    }


@router.post("/api/v1/session/logout")
async def logout(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
) -> Response:
    store.revoke(request.cookies.get(settings.cookie_name))
    response = Response(status_code=204)
    response.delete_cookie(settings.cookie_name, path="/")
    return response

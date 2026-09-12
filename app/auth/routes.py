"""Session endpoints backing the KiCad remote-provider auth handshake.

Flow, once KiCad has an access token:

    KiCad  --POST {access_token, next_url}--> /api/v1/session/bootstrap
           <--------- {nonce_url} ----------
    WebView --GET--> /session/consume?n=...  -> Set-Cookie, 302 to next_url
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.oidc import OidcVerifier, TokenError
from app.auth.session import Session, SessionStore
from app.config import Settings, get_settings

router = APIRouter()


class BootstrapRequest(BaseModel):
    access_token: str = Field(min_length=1)
    next_url: str | None = None


class BootstrapResponse(BaseModel):
    nonce_url: str


def _same_origin(candidate: str, origin: str) -> bool:
    left, right = urlsplit(candidate), urlsplit(origin)
    return (left.scheme, left.hostname, left.port) == (right.scheme, right.hostname, right.port)


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


def require_session(session: Session | None = Depends(current_session)) -> Session:
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


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
    if not _same_origin(next_url, settings.public_url):
        next_url = settings.panel_url

    nonce = store.mint_nonce(principal.subject, principal.claims, next_url)
    return BootstrapResponse(nonce_url=f"{settings.public_url}/session/consume?n={nonce}")


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
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        key=settings.cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


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

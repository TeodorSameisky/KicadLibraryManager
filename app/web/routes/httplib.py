"""KiCad's HTTP library, and the personal tokens it is read with: over HTTP.

`/kicad-httplib/v1/...` is what KiCad asks, with a token, in the shapes its
HTTP library expects; what is in them is `app.services.httplib`. The tokens
are made, listed and revoked by a person signed in, at `/kicad-library`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.auth.sessions import Session
from app.auth.tokens import TokenStore, TooManyTokens
from app.config import Settings
from app.domain import policy
from app.domain.policy import Actor
from app.errors import Invalid, NotAllowed, NotFound
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import httplib
from app.web.deps import (
    get_actor,
    get_library,
    get_settings,
    get_snapshot,
    get_tokens,
    require_access,
    require_reader,
)
from app.web.schemas.httplib import (
    HttpCategoryOut,
    HttpPartOut,
    HttpRoot,
    TokenBody,
    TokenCreated,
    TokenList,
    TokenOut,
    TokenRevoked,
)
from app.web.templating import render

PREFIX = "/kicad-httplib"

kicad = APIRouter(
    prefix=f"{PREFIX}/{httplib.API_VERSION}",
    tags=["kicad http library"],
    dependencies=[Depends(require_reader)],
)
api = APIRouter(prefix="/api/v1/tokens", tags=["tokens"], dependencies=[Depends(require_access)])
pages = APIRouter(include_in_schema=False, dependencies=[Depends(require_access)])


def root_url(settings: Settings) -> str:
    """What the `.kicad_httplib` file names: KiCad adds the version to it."""
    return f"{settings.public_url}{PREFIX}"


# -- what KiCad asks ------------------------------------------------------------


@kicad.get("/")
async def endpoints(settings: Settings = Depends(get_settings)) -> HttpRoot:
    """What KiCad asks first, to see that this is a library."""
    base = f"{root_url(settings)}/{httplib.API_VERSION}"
    return HttpRoot(categories=f"{base}/categories.json", parts=f"{base}/parts")


@kicad.get("/categories.json")
async def categories(snapshot: Snapshot = Depends(get_snapshot)) -> list[HttpCategoryOut]:
    """The categories, each shown in KiCad's symbol chooser as a library."""
    return [HttpCategoryOut.of(category) for category in httplib.categories(snapshot)]


@kicad.get("/parts/category/{category}.json")
async def parts_in_category(
    category: str,
    snapshot: Snapshot = Depends(get_snapshot),
    settings: Settings = Depends(get_settings),
) -> list[HttpPartOut]:
    """A category's parts, each with every field."""
    return [
        HttpPartOut.of(part) for part in httplib.parts_in(snapshot, category, settings.placement)
    ]


@kicad.get("/parts/{ipn}.json")
async def part(
    ipn: str,
    snapshot: Snapshot = Depends(get_snapshot),
    settings: Settings = Depends(get_settings),
) -> HttpPartOut:
    """One part, as KiCad places it."""
    found = httplib.part(snapshot, ipn, settings.placement)
    if found is None:
        raise NotFound(f"There is no part {ipn} to place.")
    return HttpPartOut.of(found)


# -- tokens -------------------------------------------------------------------


def _signed_in(session: Session | None) -> Session:
    if session is None:
        raise Invalid(
            "Sign-in is switched off on this deployment, so the HTTP library needs no token."
        )
    return session


@api.get("")
async def list_tokens(
    session: Session | None = Depends(require_access),
    actor: Actor = Depends(get_actor),
    tokens: TokenStore = Depends(get_tokens),
) -> TokenList:
    """Your tokens; a librarian's list has everyone's."""
    mine = _signed_in(session)
    found = tokens.list(None if policy.may_revoke_any_token(actor) else mine.subject)
    return TokenList(tokens=[TokenOut.of(token, asker=mine.subject) for token in found])


@api.post("", status_code=201)
async def create_token(
    body: TokenBody,
    session: Session | None = Depends(require_access),
    settings: Settings = Depends(get_settings),
    tokens: TokenStore = Depends(get_tokens),
) -> TokenCreated:
    """Make a token, and the `.kicad_httplib` file to use it in. The token is
    shown this once."""
    mine = _signed_in(session)
    try:
        token, secret = tokens.create(mine.principal, body.label)
    except TooManyTokens as exc:
        raise Invalid(str(exc)) from exc
    return TokenCreated(
        token=TokenOut.of(token, asker=mine.subject),
        secret=secret,
        library_file=httplib.library_file(
            name=settings.provider_name, root_url=root_url(settings), token=secret
        ),
    )


@api.delete("/{token_id}")
async def revoke_token(
    token_id: int,
    session: Session | None = Depends(require_access),
    actor: Actor = Depends(get_actor),
    tokens: TokenStore = Depends(get_tokens),
) -> TokenRevoked:
    """Revoke a token: your own, or anyone's for a librarian."""
    mine = _signed_in(session)
    anyone = policy.may_revoke_any_token(actor)
    if not tokens.revoke(token_id, None if anyone else mine.subject):
        if not anyone and any(token.id == token_id for token in tokens.list()):
            raise NotAllowed("Only a librarian can revoke someone else's token.")
        raise NotFound("There is no such token.")
    return TokenRevoked(id=token_id)


# -- the page -----------------------------------------------------------------


@pages.get("/kicad-library")
async def kicad_library_page(
    request: Request,
    session: Session | None = Depends(require_access),
    actor: Actor = Depends(get_actor),
    settings: Settings = Depends(get_settings),
    tokens: TokenStore = Depends(get_tokens),
    library: LibraryService = Depends(get_library),
) -> Response:
    listed = []
    if session is not None:
        anyone = policy.may_revoke_any_token(actor)
        listed = [
            TokenOut.of(token, asker=session.subject)
            for token in tokens.list(None if anyone else session.subject)
        ]
    return render(
        request,
        "kicad_library.html",
        signed_in=session is not None,
        tokens=listed,
        root_url=root_url(settings),
        sources=library.sources,
        # Without sign-in the file needs no token, and is the same for
        # everyone: it is given here as it is.
        open_file=None
        if settings.auth_configured
        else httplib.library_file(
            name=settings.provider_name, root_url=root_url(settings), token="none"
        ),
    )


router = APIRouter()
router.include_router(kicad)
router.include_router(api)
router.include_router(pages)

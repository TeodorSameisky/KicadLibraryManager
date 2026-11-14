"""Putting the application together.

Assembly only: what exists, how the pieces are joined, and what has to happen
around the first and last request. A factory rather than a module-level app,
so that a test can build one around its own settings and library.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.auth.oidc import OidcClient
from app.auth.sessions import SessionStore
from app.config import Settings
from app.library.service import LibraryService
from app.observability import RequestContextMiddleware
from app.security import SecurityHeadersMiddleware
from app.web.errors import http_error
from app.web.routes import catalog, pages, provider, session, system
from app.web.templating import STATIC_DIR

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    library: LibraryService | None = None,
    identity: OidcClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    library = library or LibraryService(settings.library_sources, Path(settings.library_workdir))
    identity = identity or OidcClient(
        metadata_url=settings.oidc_metadata_url,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        scopes=settings.oidc_scopes,
        audience=settings.oidc_audience,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        for problem in settings.problems():
            log.error("configuration: %s", problem)
        # In the background: cloning a library takes longer than a container
        # is given to become healthy.
        library.start()
        yield
        await library.stop()

    app = FastAPI(
        title=settings.provider_name,
        description="Serves an internal part library to KiCad's Remote Symbols panel.",
        version=__version__,
        lifespan=lifespan,
        root_path=settings.root_path,
    )

    app.state.settings = settings
    app.state.library = library
    app.state.identity = identity
    app.state.sessions = SessionStore(
        nonce_ttl_seconds=settings.nonce_ttl_seconds,
        login_ttl_seconds=settings.login_ttl_seconds,
        session_ttl_seconds=settings.session_ttl_seconds,
    )

    # The last one added is the outermost, so the hardening headers also land
    # on the error response the request middleware writes for a crash.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    app.add_exception_handler(StarletteHTTPException, http_error)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for module in (provider, system, session, catalog, pages):
        app.include_router(module.router)

    return app

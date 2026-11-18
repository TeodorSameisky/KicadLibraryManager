"""Putting the application together.

Assembly only: what exists, how the pieces are joined, and what has to happen
around the first and last request. A factory rather than a module-level app,
so that a test can build one around its own settings and library.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.auth.oidc import OidcClient
from app.auth.sessions import SessionStore
from app.auth.tokens import TokenStore
from app.boms.store import BomStore
from app.config import Settings
from app.db.backup import Backups, keep_backups
from app.db.database import Database
from app.errors import AppError
from app.library.publish import Identity, Publisher
from app.library.service import LibraryService
from app.library.sources import sync as sync_source
from app.observability import RequestContextMiddleware
from app.security import SecurityHeadersMiddleware
from app.services.supply import Rate, Supply
from app.suppliers.digikey import DigiKey
from app.suppliers.farnell import Farnell
from app.suppliers.mouser import Mouser
from app.suppliers.store import SupplierStore
from app.web.errors import app_error, http_error
from app.web.routes import assets as asset_routes
from app.web.routes import backup as backup_routes
from app.web.routes import boms as bom_routes
from app.web.routes import catalog, pages, provider, session, system, template_admin
from app.web.routes import editing as editing_routes
from app.web.routes import history as history_routes
from app.web.routes import httplib as httplib_routes
from app.web.routes import library as library_routes
from app.web.routes import manufacturers as manufacturer_routes
from app.web.routes import products as product_routes
from app.web.routes import supply as supply_routes
from app.web.templating import STATIC_DIR, RevalidatedStaticFiles

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    library: LibraryService | None = None,
    identity: OidcClient | None = None,
    boms: BomStore | None = None,
    backups: Backups | None = None,
    supply: Supply | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    library = library or LibraryService(
        settings.library_sources,
        Path(settings.library_workdir),
        refresh_interval_seconds=settings.library_refresh_minutes * 60,
        # The token goes to the sources marked private and to no others.
        sync=partial(sync_source, token=settings.library_token),
        token=settings.library_token,
        # Only with a token: without one a push would be refused anyway, and
        # the pages should not offer what cannot be done.
        publisher=Publisher(
            committer=Identity(settings.provider_name, f"noreply@{settings.hostname}"),
            token=settings.library_write_token,
        )
        if settings.library_write_token
        else None,
    )
    identity = identity or OidcClient(
        metadata_url=settings.oidc_metadata_url,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        scopes=settings.oidc_scopes,
        audience=settings.oidc_audience,
    )

    # One database, handed to everything that keeps something in it.
    boms = boms or BomStore(Database(Path(settings.database_path)))
    database = boms.database
    backups = backups or Backups(database.path, settings.backup_path, settings.backup_keep)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        for problem in settings.problems():
            log.error("configuration: %s", problem)
        # Said now rather than at the first upload, which may be weeks away.
        if (unusable := database.problem()) is not None:
            log.error("storage: %s", unusable)
        # In the background: cloning a library takes longer than a container
        # is given to become healthy.
        library.start()
        # Copies of the database, a day apart: the one thing here that cannot
        # be rebuilt from somewhere else.
        copying = asyncio.create_task(keep_backups(backups))
        yield
        copying.cancel()
        await asyncio.gather(copying, return_exceptions=True)
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
    app.state.database = database
    app.state.boms = boms
    app.state.backups = backups
    # Every supplier there is, whether or not this deployment has its keys:
    # one without is listed on the status page as not set up, and not asked.
    app.state.supply = supply or Supply(
        [
            DigiKey(
                settings.digikey_client_id,
                settings.digikey_client_secret,
                base_url=settings.digikey_api_url,
                site=settings.digikey_site,
                language=settings.digikey_language,
                currency=settings.digikey_currency,
            ),
            Mouser(settings.mouser_api_key, base_url=settings.mouser_api_url),
            Farnell(
                settings.farnell_api_key,
                store=settings.farnell_store,
                currency=settings.farnell_currency,
                base_url=settings.farnell_api_url,
            ),
        ],
        SupplierStore(database),
        limits={
            DigiKey.id: settings.digikey_daily_limit,
            Mouser.id: settings.mouser_daily_limit,
            Farnell.id: settings.farnell_daily_limit,
        },
        # What each allows besides so many a day. Mouser: thirty a minute.
        # Farnell: two a second.
        rates={Mouser.id: Rate(30, 60.0), Farnell.id: Rate(2, 1.0)},
        holds_for_seconds=settings.supplier_cache_hours * 3600,
    )
    app.state.sessions = SessionStore(
        nonce_ttl_seconds=settings.nonce_ttl_seconds,
        login_ttl_seconds=settings.login_ttl_seconds,
        session_ttl_seconds=settings.session_ttl_seconds,
        # The same file as the BOMs: one place to back up, and one place for
        # the storage check at startup to look at.
        database=database,
    )

    # Personal tokens, for KiCad's HTTP library. In the same file again.
    app.state.tokens = TokenStore(database)

    # The last one added is the outermost, so the hardening headers also land
    # on the error response the request middleware writes for a crash.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(AppError, app_error)

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    for module in (
        provider,
        system,
        session,
        catalog,
        library_routes,
        bom_routes,
        product_routes,
        supply_routes,
        backup_routes,
        editing_routes,
        template_admin,
        manufacturer_routes,
        asset_routes,
        httplib_routes,
        history_routes,
        pages,
    ):
        app.include_router(module.router)

    return app

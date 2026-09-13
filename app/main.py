"""KiCad Library Manager -- application entrypoint.

Assembly only: what exists, in what order, and what has to happen before the
first request. Every route lives in a router under `app/api` or `app/views`.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import APP_VERSION, provider
from app.api import catalog as catalog_api
from app.api import previews as previews_api
from app.api import system as system_api
from app.auth import routes as auth_routes
from app.auth.guard import NotSignedIn
from app.auth.oidc import OidcVerifier
from app.auth.session import SessionStore
from app.config import get_settings
from app.library_service import LibraryService, build_service
from app.middleware import SecurityHeadersMiddleware
from app.observability import (
    RequestContextMiddleware,
    configure_logging,
    unhandled_exception_handler,
)
from app.views import pages

BASE_DIR = Path(__file__).resolve().parent

log = logging.getLogger(__name__)


async def _initial_index(service: LibraryService) -> None:
    try:
        await service.refresh()
    except asyncio.CancelledError:
        raise
    except Exception:  # a bad source must not take the whole app down
        log.exception("initial library index failed")


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    settings = get_settings()

    # Reported rather than raised: a container that refuses to start cannot be
    # inspected, and every one of these is fixable from the deployment's own
    # settings page while the app keeps serving what it can.
    for problem in settings.problems():
        log.error("configuration: %s", problem)

    application.state.session_store = SessionStore(
        nonce_ttl_seconds=settings.nonce_ttl_seconds,
        session_ttl_seconds=settings.session_ttl_seconds,
    )
    application.state.oidc_verifier = OidcVerifier(settings)
    application.state.templates = templates

    service = build_service(settings.library_sources, Path(settings.library_workdir))
    application.state.library = service

    # Cloning a library with 3D models takes long enough that doing it here
    # would fail the container health check, so the first index runs in the
    # background and the panel reports progress until it lands.
    task = asyncio.create_task(_initial_index(service))

    yield

    task.cancel()


app = FastAPI(
    title="KiCad Library Manager",
    description="Serves KiCad symbols, footprints and 3D models to the Remote Symbols panel.",
    version=APP_VERSION,
    lifespan=lifespan,
    root_path=get_settings().root_path,
)

app.add_middleware(SecurityHeadersMiddleware, settings=get_settings())
app.add_middleware(RequestContextMiddleware)

# A refused request is answered as JSON or as a page depending on who asked;
# see app.views.pages.not_signed_in.
app.add_exception_handler(NotSignedIn, pages.not_signed_in)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(provider.router)
app.include_router(system_api.router)
app.include_router(catalog_api.router)
app.include_router(previews_api.router)
app.include_router(pages.router)
app.include_router(auth_routes.router)

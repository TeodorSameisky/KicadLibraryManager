"""KiCad Library Manager -- application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import provider
from app.api import catalog as catalog_api
from app.api import previews as previews_api
from app.auth import routes as auth_routes
from app.auth.oidc import OidcVerifier
from app.auth.session import SessionStore
from app.config import Settings, get_settings
from app.library_service import State, build_service
from app.middleware import SecurityHeadersMiddleware
from app.observability import (
    RequestContextMiddleware,
    configure_logging,
    unhandled_exception_handler,
)
from app.views import pages

BASE_DIR = Path(__file__).resolve().parent

log = logging.getLogger(__name__)


async def _initial_index(service) -> None:
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
    version="0.1.0",
    lifespan=lifespan,
    root_path=get_settings().root_path,
)

app.add_middleware(SecurityHeadersMiddleware, settings=get_settings())
app.add_middleware(RequestContextMiddleware)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(provider.router)
app.include_router(catalog_api.router)
app.include_router(previews_api.router)
app.include_router(pages.router)
app.include_router(auth_routes.router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": app.version,
            "auth_configured": settings.auth_configured,
            "root_path": settings.root_path,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is running and can serve.

    Deliberately independent of the library. A source being unreachable is not
    a reason to restart the container, and a liveness probe that fails for it
    turns a git outage into a crash loop.
    """
    return {"status": "ok", "version": app.version}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: whether there is a catalog to serve."""
    service = request.app.state.library
    snapshot = service.snapshot
    ready = service.state is State.READY and snapshot.finished_at is not None

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else service.state.value,
            "parts": snapshot.part_count(),
            "sources": len(snapshot.sources),
            "error": service.error,
        },
    )

"""KiCad Library Manager -- application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import asyncio
import logging

from app import catalog_routes, provider
from app.auth import routes as auth_routes
from app.auth.oidc import OidcVerifier
from app.auth.session import Session, SessionStore
from app.config import Settings, get_settings
from app.library_service import State, build_service
from app.middleware import SecurityHeadersMiddleware

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
    settings = get_settings()
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

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(provider.router)
app.include_router(catalog_routes.router)
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


@app.get("/panel", response_class=HTMLResponse)
async def panel(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: Session | None = Depends(auth_routes.current_session),
) -> HTMLResponse:
    """The page KiCad loads inside its Remote Symbols WebView."""
    return templates.TemplateResponse(
        request=request,
        name="panel.html",
        context={
            "auth_configured": settings.auth_configured,
            "provider_name": settings.provider_name,
            "session": session,
            "root_path": settings.root_path,
            "indexing": request.app.state.library.state is State.SYNCING,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe used by Docker and Coolify."""
    return {"status": "ok", "version": app.version}

"""KiCad Library Manager -- application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import provider
from app.auth import routes as auth_routes
from app.auth.oidc import OidcVerifier
from app.auth.session import Session, SessionStore
from app.config import Settings, get_settings

BASE_DIR = Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    application.state.session_store = SessionStore(
        nonce_ttl_seconds=settings.nonce_ttl_seconds,
        session_ttl_seconds=settings.session_ttl_seconds,
    )
    application.state.oidc_verifier = OidcVerifier(settings)
    yield


app = FastAPI(
    title="KiCad Library Manager",
    description="Serves KiCad symbols, footprints and 3D models to the Remote Symbols panel.",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(provider.router)
app.include_router(auth_routes.router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": app.version, "auth_configured": settings.auth_configured},
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
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe used by Docker and Coolify."""
    return {"status": "ok", "version": app.version}

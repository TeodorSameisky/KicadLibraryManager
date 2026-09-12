"""KiCad Library Manager — application entrypoint."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="KiCad Library Manager",
    description="Manage KiCad symbol, footprint and 3D model libraries.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": app.version},
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe used by Docker and Coolify."""
    return {"status": "ok", "version": app.version}

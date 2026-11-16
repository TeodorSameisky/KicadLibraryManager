"""Liveness and readiness.

Never behind authentication: whatever probes these has no session, and a
health check that needs one fails the container rather than the library.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import __version__
from app.library.service import LibraryService, State
from app.web.deps import get_library
from app.web.schemas.system import Health, Readiness

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz() -> Health:
    """Liveness: the process is up and answering.

    Says nothing about the library on purpose. An unreachable git host is not
    a reason to restart the container, and a liveness probe that fails for it
    turns an outage somewhere else into a crash loop here.
    """
    return Health(status="ok", version=__version__)


@router.get("/readyz", response_model=Readiness, responses={503: {"model": Readiness}})
async def readyz(library: LibraryService = Depends(get_library)) -> JSONResponse:
    """Readiness: there is a catalog to serve. 503, with the same body, until
    there is."""
    ready = library.state is State.READY
    return JSONResponse(
        status_code=200 if ready else 503, content=Readiness.of(library).model_dump()
    )

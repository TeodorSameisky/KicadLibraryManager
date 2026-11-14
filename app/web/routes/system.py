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

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and answering.

    Says nothing about the library on purpose. An unreachable git host is not
    a reason to restart the container, and a liveness probe that fails for it
    turns an outage somewhere else into a crash loop here.
    """
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(library: LibraryService = Depends(get_library)) -> JSONResponse:
    """Readiness: there is a catalog to serve."""
    ready = library.state is State.READY
    snapshot = library.snapshot
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": library.state.value,
            "parts": snapshot.part_count,
            "sources": len(snapshot.sources),
            "error": library.error,
        },
    )

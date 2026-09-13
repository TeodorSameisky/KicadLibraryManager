"""Liveness and readiness, kept apart from the catalog they report on.

Deliberately unauthenticated: an orchestrator probing these has no session,
and a health check that needs one fails the container rather than the library.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import APP_VERSION
from app.api.dependencies import get_service
from app.library_service import LibraryService, State

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is running and can serve.

    Deliberately independent of the library. A source being unreachable is not
    a reason to restart the container, and a liveness probe that fails for it
    turns a git outage into a crash loop.
    """
    return {"status": "ok", "version": APP_VERSION}


@router.get("/readyz")
async def readyz(service: LibraryService = Depends(get_service)) -> JSONResponse:
    """Readiness: whether there is a catalog to serve."""
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

"""Logging, request correlation, and error responses that do not leak.

Three things a deployed service needs and a development one can do without:
logs it can be searched by, a way to tie a user's report to the lines that
describe it, and errors that say what went wrong without describing the
container's filesystem to whoever asked.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

request_id: ContextVar[str] = ContextVar("request_id", default="-")

log = logging.getLogger(__name__)

# Paths whose success is noise: a health probe every 30 seconds drowns out
# everything that matters.
QUIET_PATHS = {"/healthz", "/readyz"}


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None, json_output: bool | None = None) -> None:
    """Install handlers. Safe to call more than once."""
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    if json_output is None:
        json_output = os.environ.get("LOG_FORMAT", "text").lower() == "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _JsonFormatter() if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    # uvicorn installs its own; let ours carry everything so the format is one.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    logging.getLogger("app").setLevel(resolved)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tags each request with an id, and logs how it went.

    The id is echoed in the response so a user reporting a failure can quote
    something that finds the exact request in the logs.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        token = request_id.set(incoming[:64] if incoming else uuid.uuid4().hex[:12])
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            log.exception(
                "%s %s failed after %.0fms", request.method, request.url.path, elapsed
            )
            request_id.reset(token)
            raise

        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id.get()

        if request.url.path not in QUIET_PATHS or response.status_code >= 400:
            log.log(
                logging.WARNING if response.status_code >= 500 else logging.INFO,
                "%s %s -> %d in %.0fms",
                request.method, request.url.path, response.status_code, elapsed,
            )

        request_id.reset(token)
        return response


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return something safe, and keep the detail in the logs.

    An exception's text routinely contains absolute paths, and a stack trace
    describes the deployment. Neither belongs in a response.
    """
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id.get(),
        },
        headers={"X-Request-ID": request_id.get()},
    )

"""Logging, request correlation, and failures that do not leak.

Three things a deployed service needs that a development one can do without:
logs that can be searched, a way to tie a user's report to the lines that
describe it, and errors that say something went wrong without describing the
container's filesystem to whoever asked.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

request_id: ContextVar[str] = ContextVar("request_id", default="-")

# A caller may supply its own id so that one trace spans several services, but
# it is written into every log line, so it is only accepted in a safe shape.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# A probe every thirty seconds drowns out everything worth reading, so these
# are only logged when they fail.
_QUIET_PATHS = {"/healthz", "/readyz"}


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, for a log aggregator."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """Send every logger through one handler. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level if level in logging.getLevelNamesMapping() else "INFO")

    # uvicorn installs handlers of its own; ours carries everything, so that
    # there is one format. Its access log would repeat the line written below.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    # httpx logs every request it makes with its whole address, and two of
    # the suppliers want their key in the address. Its warnings still show.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Gives each request an id, logs how it went, and absorbs crashes.

    The id is returned in X-Request-ID, so someone reporting a failure can
    quote something that finds the exact request in the logs. An exception
    that nothing else handled is logged in full here and answered with a
    message that says nothing: an exception's text routinely contains
    absolute paths, and a stack trace is a description of the deployment.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get("x-request-id", "")
        current = supplied if _REQUEST_ID.match(supplied) else uuid.uuid4().hex[:12]
        token = request_id.set(current)

        method, path = scope["method"], scope["path"]
        started = time.perf_counter()
        status = 500
        responded = False

        async def send_tagged(message: Message) -> None:
            nonlocal status, responded
            if message["type"] == "http.response.start":
                responded = True
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = current
            await send(message)

        try:
            await self.app(scope, receive, send_tagged)
        except Exception:
            log.exception("%s %s raised", method, path)
            if responded:
                raise  # the response is already under way; nothing to replace
            failure = JSONResponse(
                {"detail": "Internal server error", "request_id": current}, status_code=500
            )
            await failure(scope, receive, send_tagged)
        finally:
            if path not in _QUIET_PATHS or status >= 400:
                log.log(
                    logging.WARNING if status >= 500 else logging.INFO,
                    "%s %s -> %d in %.0fms",
                    method,
                    path,
                    status,
                    (time.perf_counter() - started) * 1000,
                )
            request_id.reset(token)

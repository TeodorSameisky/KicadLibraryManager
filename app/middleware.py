"""HTTP hardening applied to every response."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.config import Settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds HSTS and a few conservative hardening headers.

    HSTS is only meaningful -- and only legal per RFC 6797 -- over HTTPS, so it
    is emitted solely on secure requests. Behind a TLS-terminating proxy the
    scheme arrives in X-Forwarded-Proto, which is why that is consulted first.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    def _is_secure(self, request: Request) -> bool:
        forwarded = request.headers.get("x-forwarded-proto", "")
        if forwarded:
            # A proxy may send a comma-separated chain; the client-facing
            # scheme is the first entry.
            return forwarded.split(",")[0].strip().lower() == "https"
        return request.url.scheme == "https"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if self._settings.hsts_enabled and self._is_secure(request):
            value = f"max-age={self._settings.hsts_max_age}"
            if self._settings.hsts_include_subdomains:
                value += "; includeSubDomains"
            if self._settings.hsts_preload:
                value += "; preload"
            response.headers["Strict-Transport-Security"] = value

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

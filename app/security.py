"""Response headers that harden every reply.

Set by the app rather than the proxy in front of it: the deployment platform
has no switch for HSTS, and proxy labels written by hand are overwritten on
the next deploy.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings


def _is_https(scope: Scope) -> bool:
    """Whether the client reached us over TLS.

    Behind a proxy that terminates TLS the app itself sees plain http, and the
    real scheme arrives in X-Forwarded-Proto. A chain of proxies appends to
    that header, so the client-facing scheme is its first entry.
    """
    forwarded = Headers(scope=scope).get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return scope.get("scheme") == "https"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._hsts = self._hsts_value(settings) if settings.hsts_enabled else None

    @staticmethod
    def _hsts_value(settings: Settings) -> str:
        value = f"max-age={settings.hsts_max_age}"
        if settings.hsts_include_subdomains:
            value += "; includeSubDomains"
        if settings.hsts_preload:
            value += "; preload"
        return value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # RFC 6797: only meaningful, and only allowed, over TLS.
                if self._hsts and _is_https(scope):
                    headers["Strict-Transport-Security"] = self._hsts
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "same-origin")
            await send(message)

        await self.app(scope, receive, send_hardened)

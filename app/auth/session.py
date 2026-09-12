"""Nonce and session storage for the KiCad panel handshake.

KiCad hands us a Bearer token out-of-band (via the bootstrap endpoint) but the
WebView that renders the panel can only carry a cookie. The nonce bridges the
two: we mint a single-use, short-lived nonce tied to an authenticated subject,
hand back a URL containing it, and exchange it for a session cookie when the
WebView loads that URL.

Storage is in-process, which is correct for a single container and wrong for
several. Moving to Redis means reimplementing this interface and nothing else.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Session:
    subject: str
    claims: dict[str, Any]
    expires_at: float

    @property
    def display_name(self) -> str:
        for key in ("name", "preferred_username", "email"):
            value = self.claims.get(key)
            if value:
                return str(value)
        return self.subject


@dataclass(frozen=True)
class _Nonce:
    subject: str
    claims: dict[str, Any]
    next_url: str
    expires_at: float


class SessionStore:
    def __init__(self, nonce_ttl_seconds: int, session_ttl_seconds: int) -> None:
        self._nonce_ttl = nonce_ttl_seconds
        self._session_ttl = session_ttl_seconds
        self._nonces: dict[str, _Nonce] = {}
        self._sessions: dict[str, Session] = {}

    def mint_nonce(self, subject: str, claims: dict[str, Any], next_url: str) -> str:
        self._purge()
        nonce = secrets.token_urlsafe(32)
        self._nonces[nonce] = _Nonce(
            subject=subject,
            claims=claims,
            next_url=next_url,
            expires_at=time.time() + self._nonce_ttl,
        )
        return nonce

    def redeem_nonce(self, nonce: str) -> tuple[str, str] | None:
        """Consume a nonce, returning (session_id, next_url), or None if invalid.

        Redemption is unconditional removal: a nonce is valid exactly once, so a
        replayed or expired value must fail even though the caller cannot tell
        the two cases apart.
        """
        self._purge()
        record = self._nonces.pop(nonce, None)
        if record is None or record.expires_at < time.time():
            return None

        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = Session(
            subject=record.subject,
            claims=record.claims,
            expires_at=time.time() + self._session_ttl,
        )
        return session_id, record.next_url

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at < time.time():
            self._sessions.pop(session_id, None)
            return None
        return session

    def revoke(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def _purge(self) -> None:
        now = time.time()
        for key, value in list(self._nonces.items()):
            if value.expires_at < now:
                self._nonces.pop(key, None)
        for key, session in list(self._sessions.items()):
            if session.expires_at < now:
                self._sessions.pop(key, None)

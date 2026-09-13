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


def friendly_name(subject: str, claims: dict) -> str:
    """A human-usable label for a principal.

    The profile scope is deliberately not requested (it inflates the token past
    KiCad's credential-store limit), so name claims are often absent and the
    subject is an opaque hash. Showing all 64 characters of it helps nobody.
    """
    for key in ("name", "preferred_username", "email"):
        value = claims.get(key)
        if value:
            return str(value)
    return f"user {subject[:8]}" if len(subject) > 12 else subject


@dataclass(frozen=True)
class Session:
    subject: str
    claims: dict[str, Any]
    expires_at: float

    @property
    def display_name(self) -> str:
        return friendly_name(self.subject, self.claims)


@dataclass(frozen=True)
class _Nonce:
    subject: str
    claims: dict[str, Any]
    next_url: str
    expires_at: float


@dataclass(frozen=True)
class _PendingLogin:
    """A browser sign-in that has left for the provider and not come back.

    The PKCE verifier is kept here rather than in a cookie so that it never
    travels to the browser: the whole point of PKCE is that only the party
    that started the exchange can finish it.
    """

    code_verifier: str
    next_url: str
    expires_at: float


class SessionStore:
    def __init__(
        self,
        nonce_ttl_seconds: int,
        session_ttl_seconds: int,
        login_ttl_seconds: int = 600,
    ) -> None:
        self._nonce_ttl = nonce_ttl_seconds
        self._session_ttl = session_ttl_seconds
        self._login_ttl = login_ttl_seconds
        self._nonces: dict[str, _Nonce] = {}
        self._logins: dict[str, _PendingLogin] = {}
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

        return self.issue(record.subject, record.claims), record.next_url

    # -- browser sign-in --------------------------------------------------

    def begin_login(self, code_verifier: str, next_url: str) -> str:
        """Record a departing sign-in, returning its `state` parameter."""
        self._purge()
        state = secrets.token_urlsafe(32)
        self._logins[state] = _PendingLogin(
            code_verifier=code_verifier,
            next_url=next_url,
            expires_at=time.time() + self._login_ttl,
        )
        return state

    def finish_login(self, state: str) -> tuple[str, str] | None:
        """Consume a pending sign-in, returning (code_verifier, next_url).

        Single-use, like a nonce: `state` is what ties the callback to the
        request that started it, so a replayed one must fail even though the
        caller cannot tell replay from expiry.
        """
        self._purge()
        record = self._logins.pop(state, None)
        if record is None or record.expires_at < time.time():
            return None
        return record.code_verifier, record.next_url

    def issue(self, subject: str, claims: dict[str, Any]) -> str:
        """Mint a session directly, for a flow that owns its own handshake."""
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = Session(
            subject=subject,
            claims=claims,
            expires_at=time.time() + self._session_ttl,
        )
        return session_id

    # -- reading ----------------------------------------------------------

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
        for store in (self._nonces, self._logins, self._sessions):
            for key, value in list(store.items()):
                if value.expires_at < now:
                    store.pop(key, None)

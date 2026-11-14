"""Nonces, pending sign-ins, and the sessions they turn into.

KiCad gives this app a bearer token out of band, through the bootstrap
endpoint, but the WebView that shows the panel can only carry a cookie. A
nonce bridges the two: single-use, short-lived, tied to the person the token
belonged to, and exchanged for a session cookie when the WebView loads the
address containing it.

Everything is held in memory, which is right for one container and wrong for
several. Putting it in Redis means reimplementing this class and nothing else.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.auth.identity import Principal

T = TypeVar("T")

# Starting a sign-in needs no credentials, so the number that can be pending
# at once is bounded. Past the bound the oldest are dropped.
_MAX_ENTRIES = 10_000


class _Expiring(Generic[T]):
    """Values behind unguessable tokens, each valid for a limited time."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float]) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, T]] = {}

    def add(self, value: T) -> str:
        self._evict()
        token = secrets.token_urlsafe(32)
        self._entries[token] = (self._clock() + self._ttl, value)
        return token

    def get(self, token: str | None) -> T | None:
        if not token or token not in self._entries:
            return None
        expires, value = self._entries[token]
        if expires <= self._clock():
            del self._entries[token]
            return None
        return value

    def take(self, token: str | None) -> T | None:
        """Read a value and spend its token in the same step.

        Removal is unconditional: something valid exactly once has to fail
        when replayed, whether or not the first use succeeded.
        """
        value = self.get(token)
        self.discard(token)
        return value

    def discard(self, token: str | None) -> None:
        if token:
            self._entries.pop(token, None)

    def _evict(self) -> None:
        now = self._clock()
        for token in [t for t, (expires, _) in self._entries.items() if expires <= now]:
            del self._entries[token]
        while len(self._entries) >= _MAX_ENTRIES:
            del self._entries[next(iter(self._entries))]  # insertion order: the oldest


@dataclass(frozen=True)
class Session:
    principal: Principal

    @property
    def subject(self) -> str:
        return self.principal.subject

    @property
    def display_name(self) -> str:
        return self.principal.display_name


@dataclass(frozen=True)
class _Nonce:
    principal: Principal
    next_url: str


@dataclass(frozen=True)
class PendingLogin:
    """A browser sign-in that has left for the provider and not come back.

    The PKCE verifier waits here rather than in a cookie, so it never travels
    to the browser. The whole point of PKCE is that only the party that
    started an exchange can finish it.
    """

    code_verifier: str
    next_url: str


class SessionStore:
    def __init__(
        self,
        *,
        nonce_ttl_seconds: float,
        login_ttl_seconds: float,
        session_ttl_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._nonces: _Expiring[_Nonce] = _Expiring(nonce_ttl_seconds, clock)
        self._logins: _Expiring[PendingLogin] = _Expiring(login_ttl_seconds, clock)
        self._sessions: _Expiring[Session] = _Expiring(session_ttl_seconds, clock)

    # -- KiCad's handshake

    def mint_nonce(self, principal: Principal, next_url: str) -> str:
        return self._nonces.add(_Nonce(principal, next_url))

    def redeem_nonce(self, nonce: str) -> tuple[str, str] | None:
        """Spend a nonce for (session id, where to go next), or None."""
        record = self._nonces.take(nonce)
        if record is None:
            return None
        return self.create(record.principal), record.next_url

    # -- a browser's sign-in

    def begin_login(self, code_verifier: str, next_url: str) -> str:
        """Remember a departing sign-in, returning its `state` parameter."""
        return self._logins.add(PendingLogin(code_verifier, next_url))

    def finish_login(self, state: str) -> PendingLogin | None:
        """Spend a `state`. It ties the callback to the request that began
        the sign-in, so it works once, however that attempt then turns out."""
        return self._logins.take(state)

    # -- sessions

    def create(self, principal: Principal) -> str:
        return self._sessions.add(Session(principal))

    def get(self, session_id: str | None) -> Session | None:
        return self._sessions.get(session_id)

    def revoke(self, session_id: str | None) -> None:
        self._sessions.discard(session_id)

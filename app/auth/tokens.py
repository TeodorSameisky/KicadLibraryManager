"""Personal tokens: for what reads the library and cannot sign in.

KiCad's HTTP library has no sign-in of its own. It is a file naming an
address and a token, and KiCad sends that token with every request it makes.
So a person makes a token here, signed in as themselves, and puts it in the
file; the library then answers KiCad as it would answer them.

A token is theirs: it is made in their name, can be seen and revoked by them,
and stops working when revoked without anyone else's doing so. A librarian
can revoke anyone's, for when someone leaves.

Only a hash of each token is kept. A copy of the database then holds nothing
that could be put in a file and used. The token itself is shown once, when it
is made, and never again.

A token opens the HTTP library and nothing else: it reads, and only what
KiCad reads. Changing the library still takes a person signed in.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.auth.identity import Principal
from app.db.database import Database

# Marks a token as this app's, so a secret scanner -- or a person -- can tell
# what one found in a file is.
PREFIX = "klm_"
MAX_LABEL = 100
# Each person's tokens are bounded: one for each computer is the use.
MAX_PER_PERSON = 20
# How stale "last used" may be before it is written again. KiCad asks for
# every part it shows, and the day is what is worth knowing, not the second.
_TOUCH_AFTER_SECONDS = 300.0


@dataclass(frozen=True)
class Token:
    """A token, as it is listed: never the token itself."""

    id: int
    subject: str
    owner: str  # their name when they made it
    label: str
    created_at: float
    last_used_at: float | None


class TooManyTokens(Exception):
    pass


def _key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class TokenStore:
    def __init__(self, database: Database, clock: Callable[[], float] = time.time) -> None:
        self._database = database
        self._clock = clock

    def create(self, principal: Principal, label: str) -> tuple[Token, str]:
        """Make a token for `principal`. Returns it, and the secret to show once."""
        secret = PREFIX + secrets.token_urlsafe(32)
        label = " ".join(label.split())[:MAX_LABEL]
        now = self._clock()
        with self._database.connect() as connection:
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM tokens WHERE subject = ?", (principal.subject,)
            ).fetchone()
            if count >= MAX_PER_PERSON:
                raise TooManyTokens(
                    f"You have {count} tokens already. Revoke one you no longer use first."
                )
            cursor = connection.execute(
                "INSERT INTO tokens (key, subject, claims, label, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    _key(secret),
                    principal.subject,
                    json.dumps(dict(principal.claims), default=str),
                    label,
                    now,
                ),
            )
            token_id = int(cursor.lastrowid or 0)
        token = Token(token_id, principal.subject, principal.display_name, label, now, None)
        return token, secret

    def principal(self, secret: str | None) -> Principal | None:
        """Who a token was made by, or None if it is not one that holds."""
        if not secret or not secret.startswith(PREFIX):
            return None
        now = self._clock()
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, subject, claims, last_used_at FROM tokens WHERE key = ?",
                (_key(secret),),
            ).fetchone()
            if row is None:
                return None
            if row["last_used_at"] is None or now - row["last_used_at"] > _TOUCH_AFTER_SECONDS:
                connection.execute(
                    "UPDATE tokens SET last_used_at = ? WHERE id = ?", (now, row["id"])
                )
        try:
            claims = json.loads(row["claims"])
        except ValueError:
            claims = {}
        return Principal(row["subject"], claims if isinstance(claims, dict) else {})

    def list(self, subject: str | None = None) -> list[Token]:
        """One person's tokens, or everyone's; newest first."""
        query = "SELECT * FROM tokens"
        arguments: tuple[str, ...] = ()
        if subject is not None:
            query += " WHERE subject = ?"
            arguments = (subject,)
        with self._database.connect() as connection:
            rows = connection.execute(query + " ORDER BY created_at DESC, id DESC", arguments)
            return [self._token(row) for row in rows.fetchall()]

    def revoke(self, token_id: int, subject: str | None = None) -> bool:
        """Revoke a token: `subject`'s, or anyone's if None. Whether there was one."""
        query = "DELETE FROM tokens WHERE id = ?"
        arguments: tuple[object, ...] = (token_id,)
        if subject is not None:
            query += " AND subject = ?"
            arguments = (token_id, subject)
        with self._database.connect() as connection:
            return connection.execute(query, arguments).rowcount > 0

    @staticmethod
    def _token(row: sqlite3.Row) -> Token:
        try:
            claims = json.loads(row["claims"])
        except ValueError:
            claims = {}
        principal = Principal(row["subject"], claims if isinstance(claims, dict) else {})
        return Token(
            id=row["id"],
            subject=principal.subject,
            owner=principal.display_name,
            label=row["label"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )

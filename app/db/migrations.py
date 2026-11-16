"""The database's tables, as the numbered steps that made them.

A step, once released, is never changed: a database somewhere has already
been through it. What comes next is a new step at the end. The file remembers
how many it has been through (SQLite's `user_version`), and each of the rest
is run once, in order, the next time the file is opened.

A step and the note of having taken it are one transaction, so a step that
fails leaves the file as it was, to be tried again at the next start.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

Migration = Callable[[sqlite3.Connection], None]


class SchemaTooNew(Exception):
    """The file has been through more steps than this version of the app knows."""


def sql(*statements: str) -> Migration:
    """A step that is nothing but statements, which is most of them."""

    def run(connection: sqlite3.Connection) -> None:
        for statement in statements:
            connection.execute(statement)

    return run


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _baseline(connection: sqlite3.Connection) -> None:
    """Everything there was when steps began to be numbered.

    Written so that it can also be run over a database from before that, which
    has these tables already and no number: what is there is kept, and what
    it lacks is added. That is why this step, and only this one, says
    IF NOT EXISTS.
    """
    sql(
        """
        CREATE TABLE IF NOT EXISTS boms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project     TEXT NOT NULL,
            revision    TEXT NOT NULL DEFAULT '',
            filename    TEXT NOT NULL DEFAULT '',
            note        TEXT NOT NULL DEFAULT '',
            uploaded_at REAL NOT NULL,
            uploaded_by TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL,
            device      TEXT NOT NULL DEFAULT '',
            UNIQUE (project, revision)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS bom_lines (
            bom_id    INTEGER NOT NULL REFERENCES boms (id) ON DELETE CASCADE,
            position  INTEGER NOT NULL,
            ipn       TEXT,
            refs      TEXT NOT NULL,
            quantity  INTEGER NOT NULL,
            value     TEXT NOT NULL DEFAULT '',
            footprint TEXT NOT NULL DEFAULT '',
            dnp       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bom_id, position)
        )
        """,
        "CREATE INDEX IF NOT EXISTS bom_lines_by_ipn ON bom_lines (ipn)",
        # Only a hash of each session's cookie: a copy of the file then holds
        # nothing that could be put in a browser and used.
        """
        CREATE TABLE IF NOT EXISTS sessions (
            key     TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            claims  TEXT NOT NULL,
            expires REAL NOT NULL
        )
        """,
    )(connection)

    # The first BOMs were stored before a BOM could belong to a device.
    if "device" not in _columns(connection, "boms"):
        connection.execute("ALTER TABLE boms ADD COLUMN device TEXT NOT NULL DEFAULT ''")


# What suppliers were asked and what they answered, and how often each was
# asked on each day. See `app.suppliers.store`.
_suppliers = sql(
    """
    CREATE TABLE supplier_answers (
        key        TEXT PRIMARY KEY,   -- a hash of the supplier and what was asked
        supplier   TEXT NOT NULL,
        asked      TEXT NOT NULL,      -- what was asked, for whoever reads the file
        status     INTEGER NOT NULL,   -- the HTTP status it answered with
        body       TEXT NOT NULL,      -- the answer, as it came
        fetched_at REAL NOT NULL,
        expires_at REAL NOT NULL
    )
    """,
    "CREATE INDEX supplier_answers_by_supplier ON supplier_answers (supplier)",
    """
    CREATE TABLE supplier_calls (
        supplier TEXT NOT NULL,
        day      TEXT NOT NULL,        -- in UTC, as suppliers count theirs
        calls    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (supplier, day)
    )
    """,
)

# In order. Add to the end; never change or remove one that has been released.
MIGRATIONS: tuple[Migration, ...] = (_baseline, _suppliers)


def version(connection: sqlite3.Connection) -> int:
    """How many steps this file has been through."""
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(connection: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> None:
    """Take a database through the steps it has not been through yet.

    Raises `SchemaTooNew` for a file that is ahead of this code -- one that a
    later version of the app has written to -- and leaves it untouched: its
    tables may no longer be what this version expects.
    """
    current = version(connection)
    if current > len(migrations):
        raise SchemaTooNew(
            f"the database was written by a newer version of this app "
            f"(it is at step {current}; this version knows {len(migrations)})"
        )

    while current < len(migrations):
        # The write lock is taken before looking again: two processes starting
        # together would otherwise both find the same step still to do.
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = version(connection)
            if current < len(migrations):
                migrations[current](connection)
                current += 1
                connection.execute(f"PRAGMA user_version = {current}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

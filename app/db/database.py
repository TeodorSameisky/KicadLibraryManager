"""Where the database is, and how it is opened.

A connection is opened for each piece of work and closed after it. That costs
microseconds and means there is nothing to share between the threads the
routes run on, and nothing held open across a backup of the file.

The file is created when first needed; the directory it goes in is not. A
path that points somewhere that does not exist is a mistake to report, not a
reason to start making directories wherever a setting happens to point.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app.db.migrations import MIGRATIONS, Migration, SchemaTooNew, migrate
from app.errors import Unavailable


class DatabaseUnavailable(Unavailable):
    """The database cannot be opened or written. The message says why, for
    whoever runs the app: it may name a path on the server."""

    def detail(self) -> str:
        return "The database cannot be used right now."


class Database:
    def __init__(self, path: Path, *, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        self.path = path
        self._migrations = migrations
        self._prepared = False
        self._prepare_lock = threading.Lock()

    @staticmethod
    def of(database: Database | Path) -> Database:
        """A database, given one or the path of one."""
        return database if isinstance(database, Database) else Database(database)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A connection for one piece of work, which is one transaction: it is
        committed when the work is done, and undone if it raises.

        Raises `DatabaseUnavailable` if the file cannot be opened or used.
        """
        if not self.path.parent.is_dir():
            raise DatabaseUnavailable(
                f"the directory for the database does not exist: {self.path.parent}"
            )
        try:
            connection = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(f"the database cannot be opened: {exc}") from exc

        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._prepare(connection)
            with connection:  # one transaction: commits, or rolls back on error
                yield connection
        except sqlite3.OperationalError as exc:
            raise DatabaseUnavailable(f"the database cannot be used: {exc}") from exc
        finally:
            connection.close()

    def _prepare(self, connection: sqlite3.Connection) -> None:
        """Once for each process: bring the file's tables up to date."""
        if self._prepared:
            return
        with self._prepare_lock:
            if self._prepared:
                return
            # Readers do not block the writer, and the writer does not block
            # them. Kept by the file, so saying it again costs nothing.
            connection.execute("PRAGMA journal_mode = WAL")
            try:
                migrate(connection, self._migrations)
            except SchemaTooNew as exc:
                raise DatabaseUnavailable(str(exc)) from exc
            self._prepared = True

    def problem(self) -> str | None:
        """Why the database cannot be used right now, or None if it can."""
        try:
            with self.connect() as connection:
                connection.execute("SELECT 1")
        except DatabaseUnavailable as exc:
            return str(exc)
        return None

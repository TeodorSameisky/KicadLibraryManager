"""Copies of the database, because it is the one thing here that cannot be
rebuilt from somewhere else.

The library is in git, and git has the history. The BOMs were uploaded once,
by people who may no longer have the files. So they are copied: every day, to
a directory beside the database, keeping the last few; and on request, as a
download a librarian can put somewhere that is not this server.

A copy is made with SQLite's own backup, which is consistent while the app is
writing -- copying the file would not be. Two things are left out of it. The
sessions: a copy is for keeping and handing around, and it should hold
records, not who was signed in when. And what suppliers answered, which can
be asked again and would otherwise be most of the file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.errors import Unavailable

log = logging.getLogger(__name__)

_NAME = re.compile(r"^library-manager-(\d{4}-\d{2}-\d{2})\.db$")

# How often to look whether a backup is due. A day-long sleep would never wake
# in a process that is restarted daily, and an hour costs nothing.
CHECK_EVERY_SECONDS = 3600
# A backup this old is due another. A little under a day, so that a restart
# at the same time each day does not skip one.
DUE_AFTER_SECONDS = 20 * 3600


# Tables a copy is made without. Their rows, that is: the tables stay, so
# that a restored copy is a database the app can open.
_LEFT_OUT = ("sessions", "supplier_answers")


class BackupError(Unavailable):
    """A copy of the database could not be made."""

    def detail(self) -> str:
        # The reason names a path on the server; it goes to the log, not the page.
        return "A backup cannot be made right now."


@dataclass(frozen=True)
class BackupInfo:
    name: str
    made_at: float  # epoch seconds
    size_bytes: int


def copy_database(database: Path, destination: Path) -> None:
    """Write a consistent copy of `database` to `destination`, without sessions.

    Made beside the destination and moved into place, so that a copy that
    stops halfway is never mistaken for a backup.
    """
    if not database.is_file():
        raise BackupError("there is no database to copy yet")

    # A name of its own for each attempt, so that two copies being made at
    # once -- the daily one and somebody's request -- cannot write into the
    # same file.
    handle, name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".partial", dir=destination.parent
    )
    os.close(handle)
    partial = Path(name)
    try:
        source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            target = sqlite3.connect(partial)
            try:
                source.backup(target)
                # Who was signed in is not something to keep or hand around,
                # and a supplier's answers can be had again by asking.
                for table in _LEFT_OUT:
                    there = target.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
                    ).fetchone()
                    if there:
                        target.execute(f"DELETE FROM {table}")
                        target.commit()
                target.execute("VACUUM")
            finally:
                target.close()
        finally:
            source.close()
        os.replace(partial, destination)
    except (sqlite3.Error, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise BackupError(f"the database could not be copied: {exc}") from exc


class Backups:
    """The daily copies kept beside a database."""

    def __init__(
        self,
        database: Path,
        directory: Path,
        keep: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self.directory = directory
        self.keep = keep
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.keep > 0

    def _name_for(self, moment: float) -> str:
        return f"library-manager-{time.strftime('%Y-%m-%d', time.gmtime(moment))}.db"

    def list(self) -> list[BackupInfo]:
        """Every backup there is, newest first."""
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.iterdir():
            if _NAME.match(path.name) and path.is_file():
                stat = path.stat()
                found.append(BackupInfo(path.name, stat.st_mtime, stat.st_size))
        return sorted(found, key=lambda info: info.name, reverse=True)

    def latest(self) -> BackupInfo | None:
        backups = self.list()
        return backups[0] if backups else None

    def due(self) -> bool:
        if not self.enabled or not self.database.is_file():
            return False
        latest = self.latest()
        return latest is None or self._clock() - latest.made_at >= DUE_AFTER_SECONDS

    def run(self) -> BackupInfo:
        """Make today's backup, replacing one already made today, and prune.

        One a day: a second in the same day replaces the first, so restarts
        do not fill the directory.
        """
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            now = self._clock()
            name = self._name_for(now)
            path = self.directory / name
            copy_database(self.database, path)
            # When it was made by the clock everything else here uses: its name
            # was taken from that clock, and so is whether the next one is due.
            os.utime(path, (now, now))
            self._prune()
            return BackupInfo(name, now, path.stat().st_size)

    def _prune(self) -> None:
        for old in self.list()[self.keep :]:
            (self.directory / old.name).unlink(missing_ok=True)

    def path_of(self, name: str) -> Path | None:
        """A backup's file, if `name` is one. Only ever a name from the list:
        what comes in from a request is never used as a path."""
        known = {info.name for info in self.list()}
        return self.directory / name if name in known else None


async def keep_backups(backups: Backups, *, every: float = CHECK_EVERY_SECONDS) -> None:
    """Look every so often whether a backup is due, and make it.

    Runs until cancelled. A failure is logged and tried again later; it never
    stops the app, and never stops the next attempt.
    """
    if not backups.enabled:
        return
    while True:
        try:
            if backups.due():
                made = await asyncio.to_thread(backups.run)
                log.info("backed up the database: %s (%d bytes)", made.name, made.size_bytes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("the database could not be backed up: %s", exc)
        await asyncio.sleep(every)

"""BOMs, kept.

One SQLite file. The original upload is stored whole alongside the lines read
from it, so that nothing a later, better reader could have used is lost.

A connection is opened for each operation and closed after it. That costs
microseconds and means there is nothing to share between the threads the
routes run on, and nothing held open across a backup of the file.

The file is created when first needed; the directory it goes in is not. A
path that points somewhere that does not exist is a mistake to report, not a
reason to start making directories wherever a setting happens to point.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from app.boms.parse import BomLine, ParsedBom

_SCHEMA = """
CREATE TABLE IF NOT EXISTS boms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    revision    TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    uploaded_at REAL NOT NULL,
    uploaded_by TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,
    UNIQUE (project, revision)
);

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
);

CREATE INDEX IF NOT EXISTS bom_lines_by_ipn ON bom_lines (ipn);
"""

# The same columns wherever a BOM is listed, with its totals worked out.
_SUMMARY = """
SELECT b.id, b.project, b.revision, b.filename, b.note, b.uploaded_at, b.uploaded_by,
       COUNT(l.position) AS line_count,
       COALESCE(SUM(CASE WHEN l.dnp THEN 0 ELSE l.quantity END), 0) AS component_count
FROM boms b LEFT JOIN bom_lines l ON l.bom_id = b.id
"""


class BomExists(Exception):
    """A BOM is already stored for this project and revision."""

    def __init__(self, existing: Bom) -> None:
        super().__init__(f"a BOM for {existing.label} is already stored")
        self.existing = existing


class StoreUnavailable(Exception):
    """The database cannot be opened or written."""


@dataclass(frozen=True)
class Bom:
    id: int
    project: str
    revision: str
    filename: str
    note: str
    uploaded_at: float  # epoch seconds
    uploaded_by: str
    line_count: int
    component_count: int  # fitted; DNP lines are not counted
    lines: tuple[BomLine, ...] = ()  # only filled in by `get`

    @property
    def label(self) -> str:
        return f"{self.project} revision {self.revision}" if self.revision else self.project


@dataclass(frozen=True)
class Usage:
    """One part's place on one BOM."""

    bom: Bom
    quantity: int
    references: tuple[str, ...]
    dnp: bool


def _bom(row: sqlite3.Row) -> Bom:
    return Bom(**{key: row[key] for key in row.keys()})  # noqa: SIM118 - a Row is not a dict


class BomStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._prepared = False
        self._prepare_lock = threading.Lock()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.parent.is_dir():
            raise StoreUnavailable(
                f"the directory for the BOM database does not exist: {self.path.parent}"
            )
        try:
            connection = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise StoreUnavailable(f"the BOM database cannot be opened: {exc}") from exc

        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if not self._prepared:
                with self._prepare_lock:
                    # Readers do not block the writer, and the writer does not
                    # block them.
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(_SCHEMA)
                    self._prepared = True
            with connection:  # one transaction: commits, or rolls back on error
                yield connection
        except sqlite3.OperationalError as exc:
            raise StoreUnavailable(f"the BOM database cannot be used: {exc}") from exc
        finally:
            connection.close()

    def problem(self) -> str | None:
        """Why BOMs cannot be stored right now, or None if they can."""
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM boms LIMIT 1")
        except StoreUnavailable as exc:
            return str(exc)
        return None

    # -- writing

    def save(
        self,
        parsed: ParsedBom,
        *,
        project: str,
        revision: str = "",
        filename: str = "",
        note: str = "",
        uploaded_by: str = "",
        source: str = "",
        replace_existing: bool = False,
    ) -> Bom:
        """Keep a BOM. One per project and revision.

        Uploading the same revision again raises BomExists unless told to
        replace it: overwriting a record should be something a person chose.
        """
        with self._connect() as connection:
            existing = connection.execute(
                _SUMMARY + " WHERE b.project = ? AND b.revision = ? GROUP BY b.id",
                (project, revision),
            ).fetchone()
            if existing is not None:
                if not replace_existing:
                    raise BomExists(_bom(existing))
                connection.execute("DELETE FROM boms WHERE id = ?", (existing["id"],))

            cursor = connection.execute(
                "INSERT INTO boms (project, revision, filename, note, uploaded_at, uploaded_by,"
                " source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project, revision, filename, note, time.time(), uploaded_by, source),
            )
            bom_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO bom_lines (bom_id, position, ipn, refs, quantity, value, footprint,"
                " dnp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        bom_id,
                        position,
                        line.ipn,
                        " ".join(line.references),
                        line.quantity,
                        line.value,
                        line.footprint,
                        int(line.dnp),
                    )
                    for position, line in enumerate(parsed.lines)
                ],
            )
            row = connection.execute(
                _SUMMARY + " WHERE b.id = ? GROUP BY b.id", (bom_id,)
            ).fetchone()
        return replace(_bom(row), lines=parsed.lines)

    def delete(self, bom_id: int) -> bool:
        with self._connect() as connection:
            return connection.execute("DELETE FROM boms WHERE id = ?", (bom_id,)).rowcount > 0

    # -- reading

    def list(self) -> list[Bom]:
        """Every BOM, by project, each project's newest upload first."""
        with self._connect() as connection:
            rows = connection.execute(
                _SUMMARY
                + " GROUP BY b.id ORDER BY b.project COLLATE NOCASE, b.uploaded_at DESC, b.id DESC"
            ).fetchall()
        return [_bom(row) for row in rows]

    def get(self, bom_id: int) -> Bom | None:
        with self._connect() as connection:
            row = connection.execute(
                _SUMMARY + " WHERE b.id = ? GROUP BY b.id", (bom_id,)
            ).fetchone()
            if row is None:
                return None
            lines = connection.execute(
                "SELECT ipn, refs, quantity, value, footprint, dnp FROM bom_lines"
                " WHERE bom_id = ? ORDER BY position",
                (bom_id,),
            ).fetchall()
        return replace(
            _bom(row),
            lines=tuple(
                BomLine(
                    references=tuple(line["refs"].split()),
                    quantity=line["quantity"],
                    ipn=line["ipn"],
                    value=line["value"],
                    footprint=line["footprint"],
                    dnp=bool(line["dnp"]),
                )
                for line in lines
            ),
        )

    def source(self, bom_id: int) -> str | None:
        """The file as it was uploaded."""
        with self._connect() as connection:
            row = connection.execute("SELECT source FROM boms WHERE id = ?", (bom_id,)).fetchone()
        return row["source"] if row else None

    def where_used(self, ipn: str) -> list[Usage]:
        """Every BOM a part is on, by project, each project's newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT u.refs AS u_refs, u.quantity AS u_quantity, u.dnp AS u_dnp, s.* FROM"
                " bom_lines u JOIN (" + _SUMMARY + " GROUP BY b.id) s ON s.id = u.bom_id"
                " WHERE u.ipn = ? ORDER BY s.project COLLATE NOCASE, s.uploaded_at DESC, s.id DESC,"
                " u.position",
                (ipn,),
            ).fetchall()
        return [
            Usage(
                bom=Bom(**{k: row[k] for k in row.keys() if not k.startswith("u_")}),  # noqa: SIM118
                quantity=row["u_quantity"],
                references=tuple(row["u_refs"].split()),
                dnp=bool(row["u_dnp"]),
            )
            for row in rows
        ]

"""BOMs, kept.

In the database (`app.db`), which is where the tables are made. The original
upload is stored whole alongside the lines read from it, so that nothing a
later, better reader could have used is lost.
"""

from __future__ import annotations

import builtins
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from app.boms.parse import BomLine, ParsedBom
from app.db.database import Database, DatabaseUnavailable
from app.errors import Conflict, Unavailable

# The same columns wherever a BOM is listed, with its totals worked out.
_SUMMARY = """
SELECT b.id, b.project, b.revision, b.filename, b.note, b.uploaded_at, b.uploaded_by, b.device,
       COUNT(l.position) AS line_count,
       COALESCE(SUM(CASE WHEN l.dnp THEN 0 ELSE l.quantity END), 0) AS component_count
FROM boms b LEFT JOIN bom_lines l ON l.bom_id = b.id
"""


class BomExists(Conflict):
    """A BOM is already stored for this project and revision."""

    def __init__(self, existing: Bom) -> None:
        super().__init__(f"a BOM for {existing.label} is already stored")
        self.existing = existing


class StoreUnavailable(Unavailable):
    """The database cannot be opened or written."""

    def detail(self) -> str:
        # The reason names a path on the server, which is for whoever runs
        # it: the status page and the log say it, an answer does not.
        return "BOMs cannot be stored or read right now."


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
    # The device this is the BOM of, as "family/device" -- or "" when it was
    # never attached to one. What the product tree says about the key is not
    # kept here: the tree is in git and can change under a stored BOM.
    device: str = ""
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
    def __init__(self, database: Database | Path) -> None:
        self.database = Database.of(database)

    @property
    def path(self) -> Path:
        return self.database.path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection for one operation, which is one transaction."""
        try:
            with self.database.connect() as connection:
                yield connection
        except DatabaseUnavailable as exc:
            raise StoreUnavailable(str(exc)) from exc

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
        device: str = "",
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
                " source, device) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project, revision, filename, note, time.time(), uploaded_by, source, device),
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

    def attach(self, bom_id: int, device: str) -> bool:
        """Say which device a BOM belongs to, or "" for none. False if there is no such BOM."""
        with self._connect() as connection:
            return (
                connection.execute(
                    "UPDATE boms SET device = ? WHERE id = ?", (device, bom_id)
                ).rowcount
                > 0
            )

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

    # `builtins.list`, because inside this class `list` is the method above.
    def where_used(self, ipn: str) -> builtins.list[Usage]:
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

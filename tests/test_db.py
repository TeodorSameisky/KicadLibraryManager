"""The database: how it is opened, and how its tables come to be."""

import sqlite3

import pytest

from app.auth.identity import Principal
from app.auth.sessions import SessionStore
from app.boms.parse import parse_bom
from app.boms.store import BomStore
from app.db.database import Database, DatabaseUnavailable
from app.db.migrations import MIGRATIONS, sql, version
from tests.test_boms import KICAD_DEFAULT

# Everything that is kept, each with a store of its own.
TABLES = {"boms", "bom_lines", "sessions", "supplier_answers", "supplier_calls", "tokens"}


def tables(path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows if not name.startswith("sqlite_")}


def step(path) -> int:
    with sqlite3.connect(path) as connection:
        return version(connection)


# -- opening ------------------------------------------------------------------


def test_a_new_database_is_made_with_every_table_when_first_used(tmp_path):
    database = Database(tmp_path / "app.db")
    assert not database.path.exists(), "not before it is needed"

    assert database.problem() is None

    assert tables(database.path) == TABLES
    assert step(database.path) == len(MIGRATIONS)


def test_a_piece_of_work_is_one_transaction(tmp_path):
    database = Database(tmp_path / "app.db", migrations=[sql("CREATE TABLE things (name TEXT)")])

    with database.connect() as connection:
        connection.execute("INSERT INTO things VALUES ('kept')")
    with pytest.raises(RuntimeError), database.connect() as connection:
        connection.execute("INSERT INTO things VALUES ('undone')")
        raise RuntimeError("something went wrong halfway")

    with database.connect() as connection:
        assert [row["name"] for row in connection.execute("SELECT name FROM things")] == ["kept"]


def test_a_path_that_points_nowhere_is_said_and_nothing_is_made(tmp_path):
    database = Database(tmp_path / "missing" / "app.db")

    assert "does not exist" in database.problem()
    with pytest.raises(DatabaseUnavailable), database.connect():
        pass
    assert not (tmp_path / "missing").exists()


def test_everything_kept_shares_the_one_file(tmp_path):
    database = Database(tmp_path / "app.db")
    boms = BomStore(database)
    sessions = SessionStore(
        nonce_ttl_seconds=1, login_ttl_seconds=1, session_ttl_seconds=3600, database=database
    )

    boms.save(parse_bom(KICAD_DEFAULT), project="Amplifier", source="x")
    sessions.create(Principal("user-1"))

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM boms").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


# -- the steps ----------------------------------------------------------------


def test_steps_are_taken_once_and_in_order(tmp_path):
    path = tmp_path / "app.db"
    first = sql("CREATE TABLE things (name TEXT)", "INSERT INTO things VALUES ('first')")
    second = sql("ALTER TABLE things ADD COLUMN size INTEGER NOT NULL DEFAULT 0")

    assert Database(path, migrations=[first]).problem() is None
    assert step(path) == 1

    # A later version of the app, with one more step: only that one is taken.
    assert Database(path, migrations=[first, second]).problem() is None
    assert step(path) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name, size FROM things").fetchall() == [("first", 0)]

    # And opened again, nothing is taken at all.
    assert Database(path, migrations=[first, second]).problem() is None
    assert step(path) == 2


def test_a_step_that_fails_leaves_the_file_as_it_was(tmp_path):
    path = tmp_path / "app.db"
    good = sql("CREATE TABLE things (name TEXT)")
    bad = sql("CREATE TABLE more (name TEXT)", "CREATE TABLE things (name TEXT)")  # exists

    assert "cannot be used" in Database(path, migrations=[good, bad]).problem()

    assert step(path) == 1, "the step before it stays taken"
    assert tables(path) == {"things"}, "and nothing of the failed one is left behind"


def test_a_file_written_by_a_newer_version_is_left_alone(tmp_path):
    """After going back a version. Its tables may not be what this one expects."""
    path = tmp_path / "app.db"
    assert Database(path).problem() is None
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")

    older = Database(path)

    assert "newer version" in older.problem()
    with pytest.raises(DatabaseUnavailable), older.connect():
        pass
    assert step(path) == len(MIGRATIONS) + 1


def test_a_database_from_before_steps_were_numbered_is_taken_as_it_is(tmp_path):
    """The deployments that exist: the tables are there, with records in them,
    and the file has no number. Nothing is lost, and what it lacks is added."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        # As the first version made them: no device, no sessions.
        connection.executescript(
            """
            CREATE TABLE boms (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
                revision TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '', uploaded_at REAL NOT NULL,
                uploaded_by TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
                UNIQUE (project, revision)
            );
            CREATE TABLE bom_lines (
                bom_id INTEGER NOT NULL REFERENCES boms (id) ON DELETE CASCADE,
                position INTEGER NOT NULL, ipn TEXT, refs TEXT NOT NULL,
                quantity INTEGER NOT NULL, value TEXT NOT NULL DEFAULT '',
                footprint TEXT NOT NULL DEFAULT '', dnp INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (bom_id, position)
            );
            INSERT INTO boms (project, uploaded_at, source) VALUES ('Old', 1.0, 'x');
            INSERT INTO bom_lines (bom_id, position, ipn, refs, quantity)
                VALUES (1, 0, '1102-0001', 'R1 R2', 2);
            """
        )
    assert step(path) == 0

    store = BomStore(path)
    kept = store.list()

    assert [(bom.project, bom.device, bom.line_count) for bom in kept] == [("Old", "", 1)]
    assert store.get(kept[0].id).lines[0].references == ("R1", "R2")
    assert tables(path) == TABLES
    assert step(path) == len(MIGRATIONS)

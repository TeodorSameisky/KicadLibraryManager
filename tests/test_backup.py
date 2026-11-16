"""Copies of the database: the one thing that cannot be rebuilt from elsewhere."""

import asyncio
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from app.auth.identity import Principal
from app.auth.sessions import SessionStore
from app.boms.parse import parse_bom
from app.boms.store import BomStore
from app.db.backup import (
    DUE_AFTER_SECONDS,
    BackupError,
    Backups,
    copy_database,
    keep_backups,
)
from tests.conftest import OPEN, sign_in, sign_in_librarian
from tests.test_boms import KICAD_DEFAULT

DAY = 24 * 3600
# 2026-11-15 12:00:00 UTC
NOON = 1_794_744_000.0


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture()
def database(tmp_path):
    """A database with a BOM in it, and somebody signed in."""
    path = tmp_path / "app.db"
    BomStore(path).save(parse_bom(KICAD_DEFAULT), project="Amplifier", revision="A", source="x")
    SessionStore(
        nonce_ttl_seconds=1, login_ttl_seconds=1, session_ttl_seconds=3600, database=path
    ).create(Principal("user-1", {"name": "Ada"}))
    return path


def tables(path):
    connection = sqlite3.connect(path)
    try:
        return {
            name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not name.startswith("sqlite_")
        }
    finally:
        connection.close()


# -- one copy ---------------------------------------------------------------


def test_a_copy_has_the_boms_and_not_who_was_signed_in(database, tmp_path):
    """It is for keeping and handing round: records, not sessions."""
    copy = tmp_path / "copy.db"

    copy_database(database, copy)

    assert tables(database)["sessions"] == 1
    assert tables(copy)["sessions"] == 0
    assert tables(copy)["boms"] == 1 and tables(copy)["bom_lines"] == 4
    assert BomStore(copy).list()[0].project == "Amplifier", "and it opens as the real thing"


def test_a_copy_leaves_the_original_alone(database, tmp_path):
    before = tables(database)
    copy_database(database, tmp_path / "copy.db")
    assert tables(database) == before


def test_a_copy_is_made_while_the_app_is_writing(database, tmp_path):
    """The reason SQLite's own backup is used: a plain file copy of a database
    mid-write is not a database."""
    writer = sqlite3.connect(database)
    writer.execute("BEGIN")
    writer.execute("DELETE FROM bom_lines")  # an uncommitted change

    copy_database(database, tmp_path / "copy.db")
    writer.rollback()
    writer.close()

    assert tables(tmp_path / "copy.db")["bom_lines"] == 4, "as of the last commit"


def test_a_copy_that_cannot_be_made_leaves_nothing_that_looks_like_one(tmp_path):
    with pytest.raises(BackupError, match="no database to copy yet"):
        copy_database(tmp_path / "missing.db", tmp_path / "copy.db")

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database" * 100)
    with pytest.raises(BackupError, match="could not be copied"):
        copy_database(broken, tmp_path / "copy.db")

    assert sorted(path.name for path in tmp_path.iterdir()) == ["broken.db"]


def test_a_database_with_no_sessions_table_is_copied_too(tmp_path):
    path = tmp_path / "boms-only.db"
    BomStore(path).save(parse_bom(KICAD_DEFAULT), project="P", source="x")
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE sessions")  # as a file from before sessions were kept

    copy_database(path, tmp_path / "copy.db")

    assert "sessions" not in tables(tmp_path / "copy.db")


# -- the daily copies -------------------------------------------------------


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def backups(database, tmp_path, clock):
    return Backups(database, tmp_path / "backups", keep=3, clock=clock)


def test_a_backup_is_named_for_the_day_and_listed(backups, clock):
    made = backups.run()

    assert made.name == "library-manager-2026-11-15.db"
    assert made.size_bytes > 0 and made.made_at > 0
    assert [info.name for info in backups.list()] == [made.name]
    assert backups.latest().name == made.name


def test_a_second_backup_the_same_day_replaces_the_first(backups, database):
    """So restarts do not fill the directory."""
    backups.run()
    BomStore(database).save(parse_bom(KICAD_DEFAULT), project="Later", source="x")

    backups.run()

    assert len(backups.list()) == 1
    assert tables(backups.directory / backups.latest().name)["boms"] == 2


def test_only_the_last_few_are_kept(backups, clock):
    for _ in range(6):
        backups.run()
        clock.now += DAY

    names = [info.name for info in backups.list()]

    assert names == [
        "library-manager-2026-11-20.db",
        "library-manager-2026-11-19.db",
        "library-manager-2026-11-18.db",
    ]


def test_things_that_are_not_backups_are_left_alone(backups):
    backups.directory.mkdir()
    (backups.directory / "notes.txt").write_text("mine")
    (backups.directory / "library-manager-today.db").write_text("not a date")

    backups.run()
    backups.keep = 1
    backups.run()

    assert sorted(p.name for p in backups.directory.iterdir()) == [
        "library-manager-2026-11-15.db",
        "library-manager-today.db",
        "notes.txt",
    ]


def test_when_a_backup_is_due(backups, clock, tmp_path):
    assert backups.due() is True, "none yet"
    backups.run()
    assert backups.due() is False, "just made"

    clock.now += DUE_AFTER_SECONDS - 60
    assert backups.due() is False
    clock.now += 120
    assert (
        backups.due() is True
    ), "a little under a day, so a restart at the same hour does not skip one"

    assert Backups(tmp_path / "no.db", tmp_path / "b", 3, clock).due() is False, "nothing to copy"
    assert Backups(backups.database, tmp_path / "b", 0, clock).due() is False, "switched off"


def test_a_backup_is_found_only_by_a_name_from_the_list(backups):
    backups.run()

    assert backups.path_of("library-manager-2026-11-15.db") is not None
    for bad in ("../app.db", "library-manager-2026-01-01.db", "", "/etc/passwd"):
        assert backups.path_of(bad) is None


# -- keeping them -----------------------------------------------------------


async def test_the_keeper_makes_a_backup_when_one_is_due(backups):
    task = asyncio.create_task(keep_backups(backups, every=0.01))
    try:
        for _ in range(200):
            if backups.latest() is not None:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert backups.latest() is not None


async def test_a_failing_backup_is_logged_and_tried_again(backups, caplog):
    attempts = []

    def failing():
        attempts.append(1)
        raise BackupError("the disk is full")

    backups.run = failing
    with caplog.at_level(logging.ERROR, logger="app.db.backup"):
        task = asyncio.create_task(keep_backups(backups, every=0.01))
        for _ in range(200):
            if len(attempts) >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(attempts) >= 3, "and it did not stop the keeper"
    assert "the disk is full" in caplog.text


async def test_a_keeper_that_is_switched_off_does_nothing(backups):
    backups.keep = 0
    await asyncio.wait_for(keep_backups(backups, every=0.01), timeout=2)  # returns at once
    assert backups.latest() is None


# -- through the web --------------------------------------------------------


@pytest.fixture()
def web(make_client):
    """Ada, a designer, with a BOM uploaded."""
    client = sign_in(make_client())
    client.post(
        "/api/v1/boms", json={"project": "Amplifier", "revision": "A", "content": KICAD_DEFAULT}
    )
    return client


def test_a_librarian_downloads_a_copy(web, tmp_path):
    librarian = sign_in_librarian(web)

    response = librarian.get("/api/v1/backup")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.sqlite3"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="library-manager-'
    )
    assert response.headers["cache-control"] == "no-store"
    copy = tmp_path / "downloaded.db"
    copy.write_bytes(response.content)
    assert BomStore(copy).list()[0].project == "Amplifier"
    assert tables(copy)["sessions"] == 0, "and holds nobody's session"


def test_nothing_is_left_behind_in_the_temporary_directory(web):
    sign_in_librarian(web).get("/api/v1/backup")
    assert list(Path(tempfile.gettempdir()).glob("klm-backup-*")) == []


def test_only_a_librarian_downloads_a_copy(web, make_client):
    assert web.get("/api/v1/backup").status_code == 403, "Ada is a designer"
    assert make_client().get("/api/v1/backup").status_code == 401


def test_with_sign_in_off_anyone_may(make_client):
    client = make_client(**OPEN)
    client.post("/api/v1/boms", json={"project": "P", "content": KICAD_DEFAULT})
    assert client.get("/api/v1/backup").status_code == 200


def test_without_a_database_a_download_says_try_later_and_not_where(make_client, tmp_path):
    client = sign_in_librarian(make_client())
    client.app.state.backups = Backups(tmp_path / "nowhere" / "app.db", tmp_path / "b", 3)

    response = client.get("/api/v1/backup")

    assert response.status_code == 503
    assert response.json() == {"detail": "A backup cannot be made right now."}
    assert "nowhere" not in response.text


def test_the_app_makes_a_first_backup_when_it_starts(make_client, tmp_path):
    """A deployment that has run for a day has one, however often it is restarted."""
    BomStore(tmp_path / "boms.db").save(parse_bom(KICAD_DEFAULT), project="P", source="x")

    client = make_client()
    backups = client.app.state.backups
    for _ in range(300):
        if backups.latest() is not None:
            break
        time.sleep(0.01)

    assert backups.latest() is not None
    assert backups.directory == tmp_path / "backups"


def test_the_status_page_says_how_the_copies_are_doing(make_client, tmp_path):
    client = make_client(**OPEN)
    assert "none yet" in client.get("/").text or "Backups" in client.get("/").text

    client.post("/api/v1/boms", json={"project": "P", "content": KICAD_DEFAULT})
    client.app.state.backups.run()
    page = client.get("/").text

    assert "Backups" in page and "1 kept, one a day" in page
    assert "overdue" not in page
    assert 'href="/api/v1/backup"' in page, "everyone is a librarian with sign-in off"


def test_the_download_is_offered_to_librarians_only(web):
    assert 'href="/api/v1/backup"' not in web.get("/").text
    assert 'href="/api/v1/backup"' not in web.get("/boms").text

    sign_in_librarian(web)
    assert 'href="/api/v1/backup"' in web.get("/").text
    assert "Download a backup of every BOM" in web.get("/boms").text


def test_a_backup_that_has_stopped_being_made_is_called_out(make_client, tmp_path):
    client = make_client(**OPEN)
    client.post("/api/v1/boms", json={"project": "P", "content": KICAD_DEFAULT})
    backups = client.app.state.backups
    backups.run()
    old = backups.directory / backups.latest().name
    stale = time.time() - 4 * DAY
    os.utime(old, (stale, stale))

    assert "overdue" in client.get("/").text


def test_switched_off_it_says_so(make_client):
    client = make_client(**OPEN, BACKUP_KEEP="0")
    page = client.get("/").text
    assert "the BOM database is not being copied" in page

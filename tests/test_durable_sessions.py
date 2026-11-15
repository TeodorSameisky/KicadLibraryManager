"""Sessions that survive a restart: a deploy must not sign everyone out."""

import logging
import sqlite3

import pytest

from app.auth.identity import Principal
from app.auth.sessions import SessionStore
from tests.conftest import sign_in

ADA = Principal("user-1", {"sub": "user-1", "name": "Ada Lovelace", "groups": ["a", "b"]})


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def database(tmp_path):
    return tmp_path / "app.db"


def store(database, clock, **kwargs):
    return SessionStore(
        nonce_ttl_seconds=120,
        login_ttl_seconds=600,
        session_ttl_seconds=3600,
        clock=clock,
        database=database,
        **kwargs,
    )


def restart(database, clock):
    """A new process: nothing in memory, the same database."""
    return store(database, clock)


def test_a_session_survives_a_restart(database, clock):
    session_id = store(database, clock).create(ADA)

    session = restart(database, clock).get(session_id)

    assert session is not None
    assert session.subject == "user-1"
    assert session.display_name == "Ada Lovelace"
    assert session.principal.claims["groups"] == ["a", "b"], "claims come back as they went in"


def test_what_was_restored_is_then_served_from_memory(database, clock):
    session_id = store(database, clock).create(ADA)
    after = restart(database, clock)
    after.get(session_id)

    sqlite3.connect(database).execute("DELETE FROM sessions").connection.commit()

    assert after.get(session_id) is not None, "no second trip to the database"


def test_a_session_expires_when_it_was_meant_to_not_when_it_was_restored(database, clock):
    session_id = store(database, clock).create(ADA)
    clock.now += 3000
    after = restart(database, clock)
    assert after.get(session_id) is not None

    clock.now += 700  # 3700s after it was created: past its hour
    assert after.get(session_id) is None, "restoring it did not give it a new hour"
    assert restart(database, clock).get(session_id) is None


def test_signing_out_ends_it_for_good(database, clock):
    live = store(database, clock)
    session_id = live.create(ADA)

    live.revoke(session_id)

    assert live.get(session_id) is None
    assert restart(database, clock).get(session_id) is None, "not after a restart either"
    live.revoke(None)  # and revoking nothing is not an error


def test_the_cookie_itself_is_not_kept(database, clock):
    """A copy of the database -- a backup, a download -- must not hold a value
    that could be put in a browser and used."""
    session_id = store(database, clock).create(ADA)

    keys = [row[0] for row in sqlite3.connect(database).execute("SELECT key FROM sessions")]

    assert len(keys) == 1 and keys[0] != session_id
    assert session_id.encode() not in database.read_bytes()
    assert restart(database, clock).get(keys[0]) is None, "and the hash is not a cookie either"


def test_something_that_was_never_a_session_is_nobody(database, clock):
    after = store(database, clock)
    assert after.get("invented") is None
    assert after.get(None) is None
    assert after.get("") is None


def test_the_table_is_kept_as_small_as_the_people_signed_in(database, clock):
    live = store(database, clock)
    for number in range(5):
        live.create(Principal(f"user-{number}"))
    clock.now += 3601

    live.create(Principal("late"))

    count = sqlite3.connect(database).execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count == 1, "the five that expired are gone"


def test_without_a_database_sessions_work_in_memory_as_before(clock):
    live = SessionStore(
        nonce_ttl_seconds=120, login_ttl_seconds=600, session_ttl_seconds=3600, clock=clock
    )
    session_id = live.create(ADA)

    assert live.get(session_id).subject == "user-1"


def test_a_database_that_cannot_be_used_does_not_stop_anyone_signing_in(tmp_path, clock, caplog):
    """Signing in must not depend on it. It is said once, not at every request."""
    broken = store(tmp_path / "no-such-directory" / "app.db", clock)

    with caplog.at_level(logging.WARNING, logger="app.auth.sessions"):
        first = broken.create(ADA)
        second = broken.create(Principal("user-2"))
        broken.revoke(second)
        broken.get("something unknown")

    assert broken.get(first).subject == "user-1"
    assert broken.get(second) is None
    warnings = [r for r in caplog.records if "cannot be kept in the database" in r.getMessage()]
    assert len(warnings) == 1
    assert "everyone out" in warnings[0].getMessage()
    assert not (tmp_path / "no-such-directory").exists(), "and it made no directory"


# -- through the app --------------------------------------------------------


def test_someone_signed_in_stays_signed_in_across_a_deploy(make_client):
    """Two apps over one database: the first is the old deployment, the
    second the new one, and the browser's cookie is all they share."""
    before = sign_in(make_client())
    cookie = dict(before.cookies)

    after = make_client()
    assert after.get("/api/v1/session/me").json() == {"authenticated": False}

    after.cookies.update(cookie)
    me = after.get("/api/v1/session/me").json()
    assert me["authenticated"] is True
    assert me["display_name"] == "Ada Lovelace"


def test_signing_out_in_the_new_deployment_signs_out_of_the_old_cookie(make_client):
    cookie = dict(sign_in(make_client()).cookies)
    after = make_client()
    after.cookies.update(cookie)

    after.post("/api/v1/session/logout")

    again = make_client()
    again.cookies.update(cookie)
    assert again.get("/api/v1/session/me").json() == {"authenticated": False}

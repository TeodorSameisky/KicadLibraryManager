"""Fixtures.

An app is built from settings, so a test that needs a different deployment
builds a different app. Nothing is reloaded and no environment is patched.
"""

from __future__ import annotations

import contextlib
import time

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.auth.identity import Principal
from app.boms.store import BomStore
from app.config import Settings
from app.db.backup import Backups
from app.library.service import LibraryService
from tests.support import FakeProvider, LibraryTree

PUBLIC_URL = "https://library.example.com"

# A deployment with sign-in switched on, which is what most tests are about.
ENVIRONMENT = {
    "PUBLIC_URL": PUBLIC_URL,
    "AUTH_ENABLED": "true",
    "OIDC_METADATA_URL": FakeProvider.metadata_url,
    "OIDC_CLIENT_ID": FakeProvider.client_id,
    "LIBRARIANS": "grace",
    # Nowhere: an app built without a store of its own must not write one to
    # wherever the default happens to point on the machine running the tests.
    "DATABASE_PATH": "/nonexistent-klm-tests/boms.db",
}

OPEN = {"AUTH_ENABLED": "false", "OIDC_METADATA_URL": None, "OIDC_CLIENT_ID": None}


def settings_for(**overrides: str | None) -> Settings:
    """Settings for the standard deployment, with some variables changed.

    None removes a variable.
    """
    environment = {**ENVIRONMENT, **overrides}
    return Settings.from_env({k: v for k, v in environment.items() if v is not None})


def wait_until_indexed(client: TestClient) -> None:
    deadline = time.monotonic() + 10
    while client.get("/readyz").status_code != 200:
        if time.monotonic() > deadline:
            raise AssertionError(f"the library never became ready: {client.get('/readyz').json()}")
        time.sleep(0.01)


def sign_in(
    client: TestClient,
    name: str = "Ada Lovelace",
    subject: str = "user-1",
    username: str = "ada",
) -> TestClient:
    """Give a client a session, as either sign-in would have.

    What the catalog's tests care about is the cookie. How one is earned is
    covered by the tests of the sign-in routes themselves. Ada is a designer;
    `sign_in_librarian` signs in Grace, who the settings name as a librarian.
    """
    settings = client.app.state.settings
    session_id = client.app.state.sessions.create(
        Principal(subject, {"sub": subject, "name": name, "preferred_username": username})
    )
    client.cookies.set(settings.cookie_name, session_id)
    return client


def sign_in_librarian(client: TestClient) -> TestClient:
    return sign_in(client, name="Grace Hopper", subject="user-2", username="grace")


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture()
def tree(tmp_path) -> LibraryTree:
    return LibraryTree(tmp_path / "library")


@pytest.fixture()
def make_client(provider, tmp_path):
    """Build an app and a client for it.

    `tree` gives the app a library, indexed through the same background start
    the real one uses. Without one there are no sources at all.
    """
    with contextlib.ExitStack() as stack:

        def build(tree: LibraryTree | None = None, **environment: str | None) -> TestClient:
            settings = settings_for(**environment)
            library = None
            if tree is not None:
                library = LibraryService(
                    [tree.source], tmp_path / "work", sync=lambda source, workdir: tree.checkout
                )
            app = create_app(
                settings,
                library=library,
                identity=provider.client(),
                boms=BomStore(tmp_path / "boms.db"),
                backups=Backups(tmp_path / "boms.db", tmp_path / "backups", settings.backup_keep),
            )
            # https, or the Secure session cookie would never be sent back.
            client = stack.enter_context(TestClient(app, base_url=settings.origin))
            if tree is not None:
                wait_until_indexed(client)
            return client

        yield build


@pytest.fixture()
def client(make_client) -> TestClient:
    """Sign-in required, nobody signed in, no library."""
    return make_client()


@pytest.fixture()
def library_client(make_client, tree) -> TestClient:
    """Signed in, with the standard library indexed."""
    return sign_in(make_client(tree.standard(model=True)))

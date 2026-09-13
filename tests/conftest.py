"""Test fixtures.

Settings are cached and the FastAPI app reads its root_path at import time, so
exercising a different environment means rebuilding both. `build_client` does
that and guarantees the cache is cleared afterwards -- without which one test's
configuration silently leaks into the next.
"""

import contextlib
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_ENV = {
    "PUBLIC_URL": "https://library.example.com",
    "AUTH_ENABLED": "true",
    "OIDC_METADATA_URL": "https://idp.example.com/.well-known/openid-configuration",
    "OIDC_CLIENT_ID": "kicad-library-manager",
}

_MANAGED_KEYS = (
    "PUBLIC_URL",
    "AUTH_ENABLED",
    "OIDC_METADATA_URL",
    "OIDC_CLIENT_ID",
    "OIDC_SCOPES",
    "OIDC_AUDIENCE",
    "HSTS_ENABLED",
    "HSTS_MAX_AGE",
    "HSTS_INCLUDE_SUBDOMAINS",
    "HSTS_PRELOAD",
)


@pytest.fixture()
def build_client(monkeypatch):
    stack = contextlib.ExitStack()

    def _build(**overrides):
        env = {**BASE_ENV, **overrides}

        for key in _MANAGED_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            if value is not None:
                monkeypatch.setenv(key, value)

        import app.config
        import app.main

        app.config.get_settings.cache_clear()
        module = importlib.reload(app.main)

        from fastapi.testclient import TestClient

        # The session cookie is Secure, so the base URL must be https or the
        # client will silently decline to send it back.
        return stack.enter_context(TestClient(module.app, base_url=env["PUBLIC_URL"]))

    yield _build

    stack.close()

    # Clear while the patched environment is still in place is not enough: the
    # next reader must re-read the restored environment, so clear again below.
    import app.config

    app.config.get_settings.cache_clear()
    monkeypatch.undo()
    app.config.get_settings.cache_clear()


@pytest.fixture()
def client(build_client):
    return build_client()


@pytest.fixture()
def sign_in():
    """Give a client a session cookie, as the bootstrap handshake would.

    The nonce is minted directly rather than through a stubbed identity
    provider: what the catalog routes care about is the cookie, and going via
    the provider would make every catalog test depend on token verification
    too. `tests/test_auth_routes.py` covers the handshake itself.
    """

    def _sign_in(client, subject="test-user", **claims):
        store = client.app.state.session_store
        nonce = store.mint_nonce(subject, {"sub": subject, "name": "Test User", **claims}, "/panel")
        client.get(f"/session/consume?n={nonce}", follow_redirects=False)
        return client

    return _sign_in


@pytest.fixture()
def signed_in_client(client, sign_in):
    return sign_in(client)

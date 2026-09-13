"""The browser's own sign-in.

A part page link lives inside a placed symbol and may be followed from a PDF
export with no KiCad running, so the browser cannot borrow KiCad's token. For
that flow this app is the OAuth2 client and runs Authorization Code + PKCE
itself.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.auth.oidc import (
    OidcVerifier,
    Principal,
    TokenError,
    code_challenge_for,
    make_code_verifier,
)

METADATA = {
    "issuer": "https://idp.example.com",
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
    "jwks_uri": "https://idp.example.com/jwks",
}

PRINCIPAL = Principal(subject="user-1", claims={"sub": "user-1", "name": "Ada Lovelace"})


class StubProvider:
    """Stands in for the identity provider's authorize and token endpoints."""

    def __init__(self, principal=None, error=None, metadata=None):
        self._principal = principal
        self._error = error
        self.metadata_doc = METADATA if metadata is None else metadata
        self.exchanges = []

    async def metadata(self):
        if self.metadata_doc is None:
            raise TokenError("No OIDC metadata URL configured")
        return self.metadata_doc

    async def authorization_url(self, *, redirect_uri, state, code_challenge):
        return await OidcVerifier.authorization_url(
            self, redirect_uri=redirect_uri, state=state, code_challenge=code_challenge
        )

    async def exchange_code(self, *, code, code_verifier, redirect_uri):
        self.exchanges.append(
            {"code": code, "code_verifier": code_verifier, "redirect_uri": redirect_uri}
        )
        if self._error:
            raise self._error
        return self._principal


@pytest.fixture()
def provider(client, stub_provider):
    return stub_provider(client, principal=PRINCIPAL)


def start(client, next_url=None):
    path = "/auth/login" + (f"?next={next_url}" if next_url else "")
    return client.get(path, follow_redirects=False)


def state_from(response):
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


# -- starting the flow -----------------------------------------------------


def test_login_redirects_to_the_provider(client, provider):
    response = start(client)

    assert response.status_code == 307
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == METADATA["authorization_endpoint"]

    query = parse_qs(target.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["kicad-library-manager"]
    assert query["redirect_uri"] == ["https://library.example.com/auth/callback"]
    assert query["code_challenge_method"] == ["S256"], "plain PKCE is worth nothing"
    assert query["code_challenge"]
    assert query["state"]


def test_the_verifier_never_reaches_the_browser(client, provider):
    """PKCE only proves anything if the browser cannot supply the verifier."""
    response = start(client)

    assert "code_verifier" not in response.headers["location"]
    assert not response.cookies


def test_each_sign_in_gets_a_fresh_state(client, provider):
    assert state_from(start(client)) != state_from(start(client))


def test_login_is_404_when_browser_sign_in_is_disabled(build_client):
    disabled = build_client(WEB_LOGIN_ENABLED="false")
    assert disabled.get("/auth/login", follow_redirects=False).status_code == 404


def test_login_is_404_when_auth_is_off(build_client):
    off = build_client(AUTH_ENABLED="false", OIDC_METADATA_URL=None, OIDC_CLIENT_ID=None)
    assert off.get("/auth/login", follow_redirects=False).status_code == 404


def test_an_unreachable_provider_is_reported_rather_than_crashing(client):
    class Broken(StubProvider):
        async def metadata(self):
            raise httpx.ConnectError("no route to host")

    client.app.state.oidc_verifier = Broken()
    assert start(client).status_code == 502


def test_a_provider_with_no_authorize_endpoint_is_reported(client, stub_provider):
    stub_provider(client, principal=PRINCIPAL, metadata={"issuer": "https://idp.example.com"})

    assert start(client).status_code == 502


# -- finishing the flow ----------------------------------------------------


def test_the_callback_sets_a_session_and_returns_to_the_page(client, provider):
    state = state_from(start(client, "/ipn/1102-0001"))

    done = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert done.status_code == 303
    assert done.headers["location"] == "https://library.example.com/ipn/1102-0001"
    assert "HttpOnly" in done.headers["set-cookie"]
    assert "Secure" in done.headers["set-cookie"]
    assert client.get("/api/v1/session/me").json()["display_name"] == "Ada Lovelace"


def test_the_code_is_exchanged_with_the_matching_verifier(client, provider):
    started = start(client)
    challenge = parse_qs(urlsplit(started.headers["location"]).query)["code_challenge"][0]

    client.get(f"/auth/callback?code=abc&state={state_from(started)}", follow_redirects=False)

    sent = provider.exchanges[0]["code_verifier"]
    digest = hashlib.sha256(sent.encode("ascii")).digest()
    assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == challenge
    assert provider.exchanges[0]["redirect_uri"] == "https://library.example.com/auth/callback"


def test_a_state_cannot_be_replayed(client, provider):
    state = state_from(start(client))

    assert (
        client.get(f"/auth/callback?code=a&state={state}", follow_redirects=False).status_code
        == 303
    )
    assert (
        client.get(f"/auth/callback?code=a&state={state}", follow_redirects=False).status_code
        == 400
    )


def test_an_unknown_state_is_refused(client, provider):
    response = client.get("/auth/callback?code=abc&state=invented", follow_redirects=False)

    assert response.status_code == 400
    assert client.get("/api/v1/session/me").json() == {"authenticated": False}


def test_a_failed_exchange_creates_no_session(client, provider):
    provider._error = TokenError("invalid_grant")
    state = state_from(start(client))

    response = client.get(f"/auth/callback?code=stale&state={state}", follow_redirects=False)

    assert response.status_code == 401
    assert client.get("/api/v1/session/me").json() == {"authenticated": False}


def test_a_refused_consent_is_reported_and_consumes_the_state(client, provider):
    state = state_from(start(client))

    refused = client.get(
        f"/auth/callback?state={state}&error=access_denied", follow_redirects=False
    )

    assert refused.status_code == 401
    assert "access_denied" in refused.json()["detail"]
    # The state is spent even though the attempt failed, so it cannot be
    # replayed against a later one.
    assert (
        client.get(f"/auth/callback?code=a&state={state}", follow_redirects=False).status_code
        == 400
    )


def test_a_callback_with_no_code_is_refused(client, provider):
    state = state_from(start(client))
    response = client.get(f"/auth/callback?state={state}", follow_redirects=False)

    assert response.status_code == 400
    assert client.get("/api/v1/session/me").json() == {"authenticated": False}


# -- where it lands --------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.net/steal",
        "//evil.example.net/steal",
        "http://library.example.com/ipn/1",
        "https://library.example.com.evil.net/x",
    ],
    ids=["offsite", "protocol-relative", "wrong-scheme", "suffix-lookalike"],
)
def test_a_next_that_leaves_the_deployment_falls_back_to_the_panel(client, provider, hostile):
    """Otherwise the freshly minted session is handed to whoever named it."""
    state = state_from(start(client, hostile))
    done = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert done.headers["location"] == "https://library.example.com/panel"


def test_an_absolute_internal_next_is_kept(client, provider):
    state = state_from(start(client, "https://library.example.com/issues"))
    done = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert done.headers["location"] == "https://library.example.com/issues"


def test_no_next_lands_on_the_panel(client, provider):
    state = state_from(start(client))
    done = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert done.headers["location"] == "https://library.example.com/panel"


# -- PKCE helpers ----------------------------------------------------------


def test_a_verifier_is_within_the_length_the_rfc_allows():
    for _ in range(20):
        verifier = make_code_verifier()
        assert 43 <= len(verifier) <= 128
        assert set(verifier) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        )


def test_the_challenge_is_unpadded_base64url_of_the_sha256():
    assert code_challenge_for("abc") == (
        base64.urlsafe_b64encode(hashlib.sha256(b"abc").digest()).decode().rstrip("=")
    )
    assert "=" not in code_challenge_for("abc")

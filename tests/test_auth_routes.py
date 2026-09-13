"""End-to-end behaviour of the bootstrap -> nonce -> cookie handshake."""

from app.auth.oidc import Principal, TokenError


class StubVerifier:
    """Stands in for the identity provider."""

    def __init__(self, principal=None, error=None):
        self._principal = principal
        self._error = error

    async def verify(self, token):
        if self._error:
            raise self._error
        return self._principal


PRINCIPAL = Principal(subject="user-1", claims={"sub": "user-1", "name": "Ada Lovelace"})


def _use(client, verifier):
    client.app.state.oidc_verifier = verifier


def test_bootstrap_rejects_invalid_token(client):
    _use(client, StubVerifier(error=TokenError("bad token")))
    response = client.post(
        "/api/v1/session/bootstrap",
        json={"access_token": "nope", "next_url": "https://library.example.com/panel"},
    )
    assert response.status_code == 401


def test_bootstrap_requires_an_access_token(client):
    _use(client, StubVerifier(principal=PRINCIPAL))
    assert client.post("/api/v1/session/bootstrap", json={"access_token": ""}).status_code == 422


def test_full_handshake_sets_a_session(client):
    _use(client, StubVerifier(principal=PRINCIPAL))

    bootstrap = client.post(
        "/api/v1/session/bootstrap",
        json={"access_token": "valid", "next_url": "https://library.example.com/panel"},
    )
    assert bootstrap.status_code == 200
    nonce_url = bootstrap.json()["nonce_url"]
    assert nonce_url.startswith("https://library.example.com/session/consume?n=")

    # KiCad requires the nonce URL to share the bootstrap endpoint's origin.
    assert client.get("/api/v1/session/me").json() == {"authenticated": False}

    nonce = nonce_url.split("n=", 1)[1]
    consumed = client.get(f"/session/consume?n={nonce}", follow_redirects=False)
    assert consumed.status_code == 303
    assert consumed.headers["location"] == "https://library.example.com/panel"

    cookie = consumed.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie, "PUBLIC_URL is https, so the cookie must be Secure"

    me = client.get("/api/v1/session/me").json()
    assert me == {"authenticated": True, "subject": "user-1", "display_name": "Ada Lovelace"}


def test_nonce_cannot_be_replayed(client):
    _use(client, StubVerifier(principal=PRINCIPAL))
    nonce_url = client.post("/api/v1/session/bootstrap", json={"access_token": "valid"}).json()[
        "nonce_url"
    ]
    nonce = nonce_url.split("n=", 1)[1]

    assert client.get(f"/session/consume?n={nonce}", follow_redirects=False).status_code == 303
    assert client.get(f"/session/consume?n={nonce}", follow_redirects=False).status_code == 400


def test_offsite_next_url_falls_back_to_the_panel(client):
    """An attacker-supplied next_url must not receive the new session."""
    _use(client, StubVerifier(principal=PRINCIPAL))
    nonce_url = client.post(
        "/api/v1/session/bootstrap",
        json={"access_token": "valid", "next_url": "https://evil.example.net/steal"},
    ).json()["nonce_url"]

    nonce = nonce_url.split("n=", 1)[1]
    consumed = client.get(f"/session/consume?n={nonce}", follow_redirects=False)
    assert consumed.headers["location"] == "https://library.example.com/panel"


def test_logout_clears_the_session(client):
    _use(client, StubVerifier(principal=PRINCIPAL))
    nonce = (
        client.post("/api/v1/session/bootstrap", json={"access_token": "valid"})
        .json()["nonce_url"]
        .split("n=", 1)[1]
    )
    client.get(f"/session/consume?n={nonce}", follow_redirects=False)

    assert client.get("/api/v1/session/me").json()["authenticated"] is True
    assert client.post("/api/v1/session/logout").status_code == 204
    assert client.get("/api/v1/session/me").json()["authenticated"] is False


def test_panel_renders_signed_out_and_signed_in(client):
    _use(client, StubVerifier(principal=PRINCIPAL))

    assert "Sign in" in client.get("/panel").text

    nonce = (
        client.post("/api/v1/session/bootstrap", json={"access_token": "valid"})
        .json()["nonce_url"]
        .split("n=", 1)[1]
    )
    client.get(f"/session/consume?n={nonce}", follow_redirects=False)

    assert "Ada Lovelace" in client.get("/panel").text

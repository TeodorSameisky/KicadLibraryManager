"""Who may read the library.

The discovery document advertises an auth type and the panel offers a sign-in
button, so a deployment that sets AUTH_ENABLED expects the library to be
closed. These tests exist because it was not: every endpoint answered anyone.
"""

import pytest

PROTECTED = [
    "/api/v1/status",
    "/api/v1/issues",
    "/api/v1/parts",
    "/api/v1/parts/1102-0001",
    "/api/v1/parts/1102-0001/assets",
    "/api/v1/parts/1102-0001/symbol.svg",
    "/api/v1/parts/1102-0001/footprint.svg",
    "/api/v1/parts/1102-0001/model.step",
    "/ipn/1102-0001",
    "/issues",
]

# Reachable without a session by design: probes have none, discovery is what
# tells KiCad how to authenticate at all, and the panel is the page that
# offers the sign-in button.
ALWAYS_OPEN = [
    "/healthz",
    "/readyz",
    "/.well-known/kicad-remote-provider",
    "/panel",
    "/",
]


@pytest.mark.parametrize("path", PROTECTED)
def test_signed_out_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ALWAYS_OPEN)
def test_these_stay_reachable_signed_out(client, path):
    assert client.get(path).status_code in (200, 503)


def test_signing_in_opens_the_catalog(signed_in_client):
    assert signed_in_client.get("/api/v1/status").status_code == 200
    assert signed_in_client.get("/api/v1/parts").status_code == 200


def test_a_browser_is_refused_with_a_page_not_json(client):
    """A placed symbol's Datasheet link opens in a browser, which cannot use
    a JSON 401 -- it needs somewhere to go."""
    response = client.get("/ipn/1102-0001", headers={"Accept": "text/html"})

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")
    assert "Sign in" in response.text


def test_the_sign_in_link_returns_to_the_page_that_was_refused(client):
    """The link is followed from a placed symbol, so the part is the
    destination -- bouncing to the panel would lose which part was wanted."""
    response = client.get("/ipn/1102-0001?x=1", headers={"Accept": "text/html"})

    assert "/auth/login?next=%2Fipn%2F1102-0001%3Fx%3D1" in response.text


def test_without_browser_sign_in_the_page_points_at_kicad(build_client):
    no_web = build_client(WEB_LOGIN_ENABLED="false")
    response = no_web.get("/ipn/1102-0001", headers={"Accept": "text/html"})

    assert "/auth/login" not in response.text
    assert "/panel" in response.text


def test_the_api_is_refused_with_json(client):
    response = client.get("/api/v1/parts", headers={"Accept": "application/json"})

    assert response.status_code == 401
    assert response.json()["detail"]


@pytest.mark.parametrize("path", PROTECTED)
def test_nothing_is_closed_when_auth_is_off(build_client, path):
    """A library on a trusted network is a supported deployment, and the
    discovery document advertises auth type "none" to say so."""
    open_client = build_client(AUTH_ENABLED="false", OIDC_METADATA_URL=None, OIDC_CLIENT_ID=None)
    assert open_client.get(path).status_code != 401


def test_logging_out_closes_the_catalog_again(signed_in_client):
    assert signed_in_client.get("/api/v1/parts").status_code == 200

    signed_in_client.post("/api/v1/session/logout")

    assert signed_in_client.get("/api/v1/parts").status_code == 401


def test_the_way_in_does_not_itself_require_being_in(client, stub_provider):
    """/auth/login is how a signed-out browser gets a session, so a guard on
    it would be a locked door with the key behind it."""
    stub_provider(client)

    assert client.get("/auth/login", follow_redirects=False).status_code == 307

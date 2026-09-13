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
    assert "not public" in response.text
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

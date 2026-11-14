"""Signing in, both ways, and what is closed to someone who has not."""

from urllib.parse import parse_qs, urlsplit

import pytest

from app.auth.oidc import code_challenge
from tests.conftest import OPEN, sign_in

HOME = "https://library.example.com"


def me(client):
    return client.get("/api/v1/session/me").json()


# -- KiCad's handshake: bootstrap -> nonce -> cookie -------------------------


def bootstrap(client, token, **body):
    return client.post("/api/v1/session/bootstrap", json={"access_token": token, **body})


def nonce_from(response):
    return response.json()["nonce_url"].split("n=", 1)[1]


def test_the_handshake_ends_with_a_session(client, provider):
    token = provider.access_token("user-1", name="Ada Lovelace")

    started = bootstrap(client, token, next_url=f"{HOME}/panel")
    assert started.status_code == 200
    assert started.json()["nonce_url"].startswith(f"{HOME}/session/consume?n=")
    assert me(client) == {"authenticated": False}, "the token alone is not a session"

    consumed = client.get(f"/session/consume?n={nonce_from(started)}", follow_redirects=False)
    assert consumed.status_code == 303
    assert consumed.headers["location"] == f"{HOME}/panel"

    cookie = consumed.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert me(client) == {
        "authenticated": True,
        "subject": "user-1",
        "display_name": "Ada Lovelace",
    }


def test_a_nonce_works_once(client, provider):
    nonce = nonce_from(bootstrap(client, provider.access_token()))

    assert client.get(f"/session/consume?n={nonce}", follow_redirects=False).status_code == 303
    assert client.get(f"/session/consume?n={nonce}", follow_redirects=False).status_code == 400
    assert client.get("/session/consume?n=invented", follow_redirects=False).status_code == 400


def test_a_token_that_does_not_verify_is_refused(client, provider):
    assert bootstrap(client, "not.a.token").status_code == 401
    assert bootstrap(client, "").status_code == 422


def test_an_unreachable_provider_is_not_the_user_s_fault(client, provider):
    """401 would tell KiCad the token is bad and send the user to sign in again."""
    token = provider.access_token()
    provider.unreachable = True

    assert bootstrap(client, token).status_code == 502


@pytest.mark.parametrize(
    "next_url",
    ["https://evil.example.net/steal", "http://library.example.com/panel", "//evil.example.net"],
)
def test_the_session_is_never_sent_off_site(client, provider, next_url):
    """An attacker-supplied next_url must not be where the cookie lands."""
    nonce = nonce_from(bootstrap(client, provider.access_token(), next_url=next_url))

    consumed = client.get(f"/session/consume?n={nonce}", follow_redirects=False)

    assert consumed.headers["location"] == f"{HOME}/panel"


def test_there_is_no_handshake_when_sign_in_is_off(make_client, provider):
    open_client = make_client(**OPEN)
    assert bootstrap(open_client, provider.access_token()).status_code == 404


def test_signing_out_ends_the_session(client):
    sign_in(client)
    assert me(client)["authenticated"] is True

    assert client.post("/api/v1/session/logout").status_code == 204

    assert me(client) == {"authenticated": False}
    assert client.post("/api/v1/session/logout").status_code == 204, "twice is harmless"


# -- a browser's own sign-in: login -> provider -> callback ------------------


def start(client, next_url=None):
    return client.get(
        "/auth/login", params={"next": next_url} if next_url else None, follow_redirects=False
    )


def query_of(response):
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


def finish(client, provider, started, **claims):
    """The user approves at the provider, which sends the browser back."""
    location = started.headers["location"]
    code = provider.approve(location, **claims)
    return client.get(
        "/auth/callback", params={"code": code, "state": query_of(started)["state"]},
        follow_redirects=False,
    )  # fmt: skip


def test_login_sends_the_browser_to_the_provider(client):
    started = start(client)

    assert started.status_code == 307
    assert started.headers["location"].startswith("https://idp.example.com/authorize?")
    query = query_of(started)
    assert query["redirect_uri"] == f"{HOME}/auth/callback"
    assert query["client_id"] == "kicad-library-manager"
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] and query["state"]


def test_the_verifier_never_reaches_the_browser(client):
    """PKCE proves something only if the browser cannot supply it."""
    started = start(client)

    assert "code_verifier" not in started.headers["location"]
    assert "set-cookie" not in started.headers


def test_every_sign_in_gets_its_own_state_and_challenge(client):
    first, second = query_of(start(client)), query_of(start(client))

    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]


def test_the_callback_sets_a_session_and_returns_to_the_page_asked_for(client, provider):
    """The link was followed from a placed symbol, so the part is where the
    reader was going -- not the panel."""
    done = finish(client, provider, start(client, "/ipn/1102-0001"), name="Ada Lovelace")

    assert done.status_code == 303
    assert done.headers["location"] == f"{HOME}/ipn/1102-0001"
    assert "HttpOnly" in done.headers["set-cookie"] and "Secure" in done.headers["set-cookie"]
    assert me(client)["display_name"] == "Ada Lovelace"


def test_the_code_is_redeemed_with_the_verifier_that_matches_the_challenge(client, provider):
    started = start(client)
    finish(client, provider, started)

    (sent,) = [r for r in provider.requests if r.url.path == "/token"]
    form = {k: v[0] for k, v in parse_qs(sent.content.decode()).items()}
    assert code_challenge(form["code_verifier"]) == query_of(started)["code_challenge"]
    assert form["redirect_uri"] == f"{HOME}/auth/callback"
    assert "client_secret" not in form, "a public client has none"


def test_a_state_works_once(client, provider):
    started = start(client)

    assert finish(client, provider, started).status_code == 303
    assert finish(client, provider, started).status_code == 400


def test_an_invented_state_is_refused(client):
    response = client.get("/auth/callback?code=abc&state=invented", follow_redirects=False)

    assert response.status_code == 400
    assert me(client) == {"authenticated": False}


def test_a_code_the_provider_rejects_creates_no_session(client):
    state = query_of(start(client))["state"]

    response = client.get(f"/auth/callback?code=stale&state={state}", follow_redirects=False)

    assert response.status_code == 401
    assert "invalid_grant" in response.json()["detail"]
    assert me(client) == {"authenticated": False}


def test_declined_consent_is_reported_and_spends_the_state(client, provider):
    started = start(client)
    state = query_of(started)["state"]

    refused = client.get(
        f"/auth/callback?state={state}&error=access_denied", follow_redirects=False
    )

    assert refused.status_code == 401
    assert "access_denied" in refused.json()["detail"]
    # Spent although the attempt failed, so it cannot be replayed at a later one.
    assert finish(client, provider, started).status_code == 400


def test_a_callback_with_no_code_is_refused(client):
    state = query_of(start(client))["state"]

    assert client.get(f"/auth/callback?state={state}", follow_redirects=False).status_code == 400
    assert me(client) == {"authenticated": False}


def test_a_failure_in_the_browser_is_a_page(client):
    """Nobody following a link should be left looking at raw JSON."""
    response = client.get(
        "/auth/callback?code=abc&state=invented",
        headers={"Accept": "text/html"}, follow_redirects=False,
    )  # fmt: skip

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "This sign-in has expired; start again" in response.text
    assert 'href="/panel"' in response.text


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.net/steal",
        "//evil.example.net/steal",
        "http://library.example.com/ipn/1",
        "https://library.example.com.evil.net/x",
    ],
    ids=["offsite", "protocol-relative", "http", "lookalike"],
)
def test_a_next_that_leaves_the_deployment_lands_on_the_panel(client, provider, hostile):
    done = finish(client, provider, start(client, hostile))
    assert done.headers["location"] == f"{HOME}/panel"


def test_no_next_lands_on_the_panel_and_an_internal_one_is_kept(client, provider):
    assert finish(client, provider, start(client)).headers["location"] == f"{HOME}/panel"

    kept = finish(client, provider, start(client, f"{HOME}/issues?severity=error"))
    assert kept.headers["location"] == f"{HOME}/issues?severity=error"


def test_under_a_prefix_the_browser_comes_back_under_it(make_client, provider):
    prefixed = make_client(PUBLIC_URL="https://sameisky.tech/kicadLibrary")

    started = start(prefixed, "/ipn/1102-0001")
    assert query_of(started)["redirect_uri"] == "https://sameisky.tech/kicadLibrary/auth/callback"

    done = finish(prefixed, provider, started)
    assert done.headers["location"] == "https://sameisky.tech/kicadLibrary/ipn/1102-0001"


def test_an_unreachable_provider_is_reported_and_leaves_nothing_pending(client, provider):
    provider.unreachable = True

    assert start(client).status_code == 502
    assert client.app.state.sessions._logins._entries == {}


def test_a_provider_with_nowhere_to_send_the_browser_is_reported(client, provider):
    del provider.metadata["authorization_endpoint"]
    assert start(client).status_code == 502


@pytest.mark.parametrize(
    "environment", [{"WEB_LOGIN_ENABLED": "false"}, OPEN], ids=["disabled", "auth-off"]
)
def test_browser_sign_in_can_be_switched_off(make_client, environment):
    off = make_client(**environment)

    assert start(off).status_code == 404
    assert off.get("/auth/callback?code=a&state=b", follow_redirects=False).status_code == 404


# -- who may read the library ------------------------------------------------

CLOSED = [
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

# Open on purpose. A probe has no session; discovery is what tells KiCad how
# to authenticate at all; the panel is the page with the sign-in button; and
# the status page is where a broken sign-in gets diagnosed.
ALWAYS_OPEN = ["/healthz", "/readyz", "/.well-known/kicad-remote-provider", "/panel", "/"]


@pytest.fixture()
def library(make_client, tree):
    """A library to protect, with nobody signed in."""
    return make_client(tree.standard(model=True))


@pytest.mark.parametrize("path", CLOSED)
def test_the_library_is_closed_to_someone_signed_out(library, path):
    assert library.get(path).status_code == 401


@pytest.mark.parametrize("path", CLOSED)
def test_signing_in_opens_it(library, path):
    assert sign_in(library).get(path).status_code == 200


@pytest.mark.parametrize("path", ALWAYS_OPEN)
def test_what_is_needed_to_get_in_stays_open(library, path):
    assert library.get(path).status_code == 200


@pytest.mark.parametrize("path", CLOSED)
def test_nothing_is_closed_when_sign_in_is_off(make_client, tree, path):
    """A library on a trusted network is a supported deployment."""
    assert make_client(tree.standard(model=True), **OPEN).get(path).status_code == 200


def test_the_open_pages_give_nothing_away(library):
    """They show counts and configuration. What is in the library comes from
    the API, which is closed."""
    for path in ("/", "/panel"):
        text = library.get(path).text
        assert "1102-0001" not in text and "Resistor 10k" not in text, path


def test_signing_out_closes_it_again(library):
    sign_in(library)
    assert library.get("/api/v1/parts").status_code == 200

    library.post("/api/v1/session/logout")

    assert library.get("/api/v1/parts").status_code == 401


def test_a_script_is_refused_with_json(library):
    response = library.get("/api/v1/parts", headers={"Accept": "application/json"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in to read this library"}


def test_a_browser_is_refused_with_a_way_in(library):
    """A placed symbol's Datasheet link opens in a browser. The link in the
    page returns to the part that was asked for, not to the panel."""
    response = library.get("/ipn/1102-0001?x=1", headers={"Accept": "text/html"})

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")
    assert 'href="/auth/login?next=%2Fipn%2F1102-0001%3Fx%3D1"' in response.text
    assert "Resistor 10k" not in response.text


def test_without_browser_sign_in_the_page_points_at_kicad(make_client, tree):
    no_web = make_client(tree.standard(), WEB_LOGIN_ENABLED="false")

    response = no_web.get("/ipn/1102-0001", headers={"Accept": "text/html"})

    assert response.status_code == 401
    assert "/auth/login" not in response.text
    assert "Remote Symbols panel in KiCad" in response.text
    assert 'href="/panel"' in response.text


def test_under_a_prefix_the_way_in_carries_it(make_client, tree):
    prefixed = make_client(tree.standard(), PUBLIC_URL="https://sameisky.tech/kicadLibrary")

    response = prefixed.get("/ipn/1102-0001", headers={"Accept": "text/html"})

    assert 'href="/kicadLibrary/auth/login?next=' in response.text

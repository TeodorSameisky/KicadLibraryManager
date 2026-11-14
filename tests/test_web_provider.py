"""The discovery document, configuration, and what every response carries."""

import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.library.sources import Source
from app.observability import configure_logging
from tests.conftest import OPEN, settings_for

# A verbatim copy of resources/schemas/ in the KiCad source tree. Refresh it
# when targeting a newer KiCad: drift shows up in production as a provider
# that KiCad silently refuses to load.
SCHEMA = json.loads(
    (
        Path(__file__).parent / "fixtures" / "kicad-remote-provider-metadata-v1.schema.json"
    ).read_text(encoding="utf-8")
)

DISCOVERY = "/.well-known/kicad-remote-provider"


# -- the discovery document -------------------------------------------------


@pytest.mark.parametrize(
    "environment",
    [{}, OPEN, {"PUBLIC_URL": "https://sameisky.tech/kicadLibrary"}, {"OIDC_SCOPES": "openid"}],
    ids=["oauth2", "open", "prefixed", "one-scope"],
)
def test_the_document_satisfies_kicad_s_own_schema(make_client, environment):
    """The schema forbids unknown keys, so one extra field fails it all."""
    jsonschema.validate(make_client(**environment).get(DISCOVERY).json(), SCHEMA)


def test_the_schema_copy_still_bites():
    """Guards the fixture: this rule is why auth is configured all or nothing."""
    oauth2_without_a_client = {
        "provider_name": "x",
        "provider_version": "1",
        "api_base_url": "https://x/api",
        "panel_url": "https://x/panel",
        "auth": {"type": "oauth2", "metadata_url": "https://idp/.well-known/openid-configuration"},
        "capabilities": {"web_ui_v1": True},
        "max_download_bytes": 1,
        "supported_asset_types": ["symbol"],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(oauth2_without_a_client, SCHEMA)


def test_with_sign_in_it_tells_kicad_how_to_authenticate(client):
    document = client.get(DISCOVERY).json()

    assert document["auth"] == {
        "type": "oauth2",
        "metadata_url": "https://idp.example.com/.well-known/openid-configuration",
        "client_id": "kicad-library-manager",
        "scopes": ["openid", "email"],
    }
    # KiCad rejects a bootstrap address on another origin than the panel.
    panel, bootstrap = urlsplit(document["panel_url"]), urlsplit(document["session_bootstrap_url"])
    assert (panel.scheme, panel.netloc) == (bootstrap.scheme, bootstrap.netloc)
    assert bootstrap.path == "/api/v1/session/bootstrap"


def test_without_sign_in_it_says_so(make_client):
    document = make_client(**OPEN).get(DISCOVERY).json()

    assert document["auth"] == {"type": "none"}
    assert "session_bootstrap_url" not in document, "only advertised when it does something"


def test_a_transport_is_always_advertised(client):
    """KiCad 10 refuses the provider otherwise: "Remote provider metadata
    must enable direct_downloads_v1, inline_payloads_v1, or both." Its schema
    allows neither, so only this catches it."""
    document = client.get(DISCOVERY).json()

    assert document["capabilities"]["web_ui_v1"] is True
    assert document["capabilities"]["inline_payloads_v1"] is True
    assert document["supported_asset_types"] == ["symbol", "footprint", "3dmodel"]
    assert document["max_download_bytes"] == 64 * 1024 * 1024


def test_the_scopes_asked_for_stay_small(client):
    """KiCad keeps its tokens in the OS credential store, and Windows gives
    it about 1280 characters. A provider that packs names and groups into the
    tokens goes over, and the login fails after it has succeeded."""
    scopes = client.get(DISCOVERY).json()["auth"]["scopes"]

    assert "openid" in scopes
    assert "profile" not in scopes


def test_every_address_carries_the_path_prefix(make_client):
    document = (
        make_client(PUBLIC_URL="https://sameisky.tech/kicadLibrary", **OPEN).get(DISCOVERY).json()
    )

    assert document["panel_url"] == "https://sameisky.tech/kicadLibrary/panel"
    assert document["api_base_url"] == "https://sameisky.tech/kicadLibrary/api/v1"


# -- configuration ----------------------------------------------------------


def test_with_nothing_set_there_is_a_working_local_default():
    settings = Settings.from_env({})

    assert settings.public_url == "http://localhost:8000"
    assert not settings.auth_configured
    assert settings.library_sources == ()
    assert settings.capability_inline_payloads
    assert not settings.cookie_secure


def test_addresses_are_derived_from_the_public_url():
    prefixed = settings_for(PUBLIC_URL="https://sameisky.tech/kicadLibrary/")
    root = settings_for(PUBLIC_URL="https://kicad.sameisky.tech")

    assert (prefixed.origin, prefixed.root_path) == ("https://sameisky.tech", "/kicadLibrary")
    assert prefixed.panel_url == "https://sameisky.tech/kicadLibrary/panel"
    # Without the prefix, the provider would send the browser back to whatever
    # else is mounted at the site root.
    assert prefixed.web_callback_url == "https://sameisky.tech/kicadLibrary/auth/callback"

    assert (root.origin, root.root_path) == ("https://kicad.sameisky.tech", "")
    assert root.session_bootstrap_url == "https://kicad.sameisky.tech/api/v1/session/bootstrap"
    assert root.cookie_secure, "https means a Secure cookie unless told otherwise"


def test_sources_are_read_from_the_environment():
    settings = settings_for(
        LIBRARY_SOURCES='[{"id":"company","url":"https://example.com/lib.git"}]',
        REMOTE_LIBRARY_PREFIX="corp",
    )

    assert settings.library_sources == (
        Source("company", "company", "https://example.com/lib.git"),
    )
    assert settings.placement.remote_library("Passives") == "corp_passives"
    assert settings.placement.part_url("1102-0001") == "https://library.example.com/ipn/1102-0001"


def test_a_healthy_deployment_has_no_problems():
    assert settings_for().problems() == []
    assert settings_for(**OPEN).problems() == []


@pytest.mark.parametrize(
    "environment,fragment",
    [
        ({"PUBLIC_URL": "http://localhost:8000", **OPEN}, "points at localhost"),
        ({"PUBLIC_URL": "library.example.com", **OPEN}, "must be an absolute URL"),
        ({"OIDC_CLIENT_ID": None}, "OIDC_CLIENT_ID is not set, so the library is open"),
        ({"OIDC_CLIENT_ID": None, "OIDC_METADATA_URL": None}, "are not set"),
        ({"PUBLIC_URL": "http://library.example.com"}, "sign-in needs https"),
        (
            {"PUBLIC_URL": "http://library.example.com", "COOKIE_SECURE": "true", **OPEN},
            "never sent",
        ),
        (
            {"CAPABILITY_DIRECT_DOWNLOADS": "false", "CAPABILITY_INLINE_PAYLOADS": "false"},
            "KiCad refuses a provider",
        ),
        ({"LIBRARY_SOURCES": "not json"}, "LIBRARY_SOURCES is unusable"),
        ({"SESSION_TTL_SECONDS": "eight hours"}, "SESSION_TTL_SECONDS must be a whole number"),
        ({"AUTH_ENABLED": "maybe"}, "AUTH_ENABLED must be true or false"),
        ({"LIBRARIANS": None}, "LIBRARIANS is empty, so nobody can refresh the library"),
    ],
)
def test_a_misconfiguration_is_named(environment, fragment):
    """Each of these otherwise surfaces much later, as a puzzle: a panel
    loaded from the user's own machine, a login that does nothing."""
    assert any(fragment in problem for problem in settings_for(**environment).problems())


def test_a_bad_value_falls_back_instead_of_stopping_the_app():
    """A container that will not start cannot be inspected."""
    settings = settings_for(SESSION_TTL_SECONDS="eight hours", LIBRARY_SOURCES="{}")

    assert settings.session_ttl_seconds == 8 * 60 * 60
    assert settings.library_sources == ()


# -- what every response carries --------------------------------------------

HSTS = "strict-transport-security"


def test_hsts_is_sent_over_https(client):
    assert client.get("/healthz").headers[HSTS] == "max-age=31536000"


def test_hsts_is_not_sent_over_plain_http(make_client):
    """RFC 6797 forbids it."""
    plain = make_client(PUBLIC_URL="http://localhost:8000", **OPEN)
    assert HSTS not in plain.get("/healthz").headers


def test_the_scheme_a_proxy_forwards_is_the_one_that_counts(make_client):
    """Behind a proxy that terminates TLS, the app itself sees plain http."""
    plain = make_client(PUBLIC_URL="http://localhost:8000", **OPEN)

    def hsts(forwarded):
        return plain.get("/healthz", headers={"X-Forwarded-Proto": forwarded}).headers.get(HSTS)

    assert hsts("https") == "max-age=31536000"
    assert hsts("http") is None
    assert hsts("https, http") == "max-age=31536000", "the client-facing scheme comes first"


def test_hsts_directives_are_opt_in(make_client, client):
    """Preload above all: getting off the list takes months."""
    both = make_client(HSTS_INCLUDE_SUBDOMAINS="true", HSTS_PRELOAD="true")

    assert both.get("/healthz").headers[HSTS] == "max-age=31536000; includeSubDomains; preload"
    assert "preload" not in client.get("/healthz").headers[HSTS]
    assert HSTS not in make_client(HSTS_ENABLED="false").get("/healthz").headers


def test_hardening_headers_are_on_every_response(client):
    for path in ("/panel", "/api/v1/parts", "/static/js/app.js", "/nowhere"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["referrer-policy"] == "same-origin", path


def test_every_response_has_an_id_to_quote(client):
    first, second = client.get("/healthz"), client.get("/healthz")

    assert len(first.headers["x-request-id"]) == 12
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_a_caller_s_own_request_id_is_kept_if_it_is_safe(client):
    kept = client.get("/healthz", headers={"X-Request-ID": "trace-42.a_b"})
    replaced = client.get("/healthz", headers={"X-Request-ID": "x" * 80})
    hostile = client.get("/healthz", headers={"X-Request-ID": "a b\tc"})

    assert kept.headers["x-request-id"] == "trace-42.a_b"
    assert replaced.headers["x-request-id"] != "x" * 80
    assert hostile.headers["x-request-id"] != "a b\tc", "it is written into every log line"


def test_a_crash_is_answered_without_describing_the_server(client, caplog):
    @client.app.get("/boom")
    async def boom():
        raise RuntimeError("secret at C:\\data\\sources\\company")

    with caplog.at_level(logging.ERROR, logger="app.observability"):
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error",
        "request_id": response.headers["x-request-id"],
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "secret at" in caplog.text, "the detail goes to the log instead"


def test_health_probes_are_only_logged_when_they_fail(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.observability"):
        client.get("/healthz")
        client.get("/readyz")  # 503: no library
        client.get("/panel")

    logged = [record.getMessage() for record in caplog.records]
    assert not any("/healthz" in line for line in logged)
    assert any("GET /readyz -> 503" in line for line in logged)
    assert any("GET /panel -> 200" in line for line in logged)


def test_an_ordinary_refusal_is_not_a_crash(client):
    @client.app.get("/teapot")
    async def teapot():
        raise HTTPException(status_code=418, detail="short and stout")

    assert client.get("/teapot").json() == {"detail": "short and stout"}


def test_logs_can_be_json(capsys):
    root = logging.getLogger()
    before = root.handlers[:], root.level
    try:
        configure_logging("INFO", json_output=True)
        logging.getLogger("app.test").info("indexed %d parts", 3)
        entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    finally:
        root.handlers[:], root.level = before[0], before[1]
        logging.getLogger("uvicorn.access").disabled = False

    assert entry["message"] == "indexed 3 parts"
    assert (entry["level"], entry["logger"], entry["request_id"]) == ("INFO", "app.test", "-")

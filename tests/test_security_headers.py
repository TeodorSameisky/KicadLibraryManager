"""HSTS and hardening headers."""

import pytest

HSTS = "strict-transport-security"


def test_hsts_sent_on_https(client):
    """The test client's base URL is https, so the header must appear."""
    headers = client.get("/healthz").headers
    assert headers[HSTS] == "max-age=31536000"


def test_hsts_absent_over_plain_http(build_client):
    """RFC 6797: HSTS must not be emitted on an insecure request."""
    http_client = build_client(PUBLIC_URL="http://localhost:8000", AUTH_ENABLED="false",
                               OIDC_METADATA_URL=None, OIDC_CLIENT_ID=None)
    assert HSTS not in http_client.get("/healthz").headers


def test_forwarded_proto_is_honoured(build_client):
    """Behind a TLS-terminating proxy the app itself sees plain http."""
    http_client = build_client(PUBLIC_URL="http://localhost:8000", AUTH_ENABLED="false",
                               OIDC_METADATA_URL=None, OIDC_CLIENT_ID=None)

    secure = http_client.get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert secure.headers[HSTS] == "max-age=31536000"

    insecure = http_client.get("/healthz", headers={"X-Forwarded-Proto": "http"})
    assert HSTS not in insecure.headers

    # Proxies may append to an existing chain; the client-facing scheme leads.
    chained = http_client.get("/healthz", headers={"X-Forwarded-Proto": "https, http"})
    assert chained.headers[HSTS] == "max-age=31536000"


def test_optional_directives(build_client):
    c = build_client(HSTS_INCLUDE_SUBDOMAINS="true", HSTS_PRELOAD="true")
    value = c.get("/healthz").headers[HSTS]
    assert value == "max-age=31536000; includeSubDomains; preload"


def test_preload_is_off_by_default(client):
    """Preload is effectively irreversible, so it must never be implicit."""
    assert "preload" not in client.get("/healthz").headers[HSTS]


def test_hsts_can_be_disabled(build_client):
    c = build_client(HSTS_ENABLED="false")
    assert HSTS not in c.get("/healthz").headers


def test_hardening_headers_present(client):
    headers = client.get("/panel").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"

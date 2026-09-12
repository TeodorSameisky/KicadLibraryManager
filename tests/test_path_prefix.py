"""Behaviour when the app is mounted under a reverse-proxy path prefix.

Coolify routes http://host/kicadLibrary/* to this app with the prefix stripped,
so the app sees "/panel" while the browser sits at "/kicadLibrary/panel". Every
URL we emit must carry the prefix back.
"""

import pytest

from app.auth.routes import _is_internal_url

PREFIXED = "https://sameisky.tech/kicadLibrary"


@pytest.fixture()
def prefixed_client(build_client):
    return build_client(
        PUBLIC_URL=PREFIXED,
        AUTH_ENABLED="false",
        OIDC_METADATA_URL=None,
        OIDC_CLIENT_ID=None,
    )


def test_settings_split_prefix_and_origin(prefixed_client):
    from app.config import get_settings

    settings = get_settings()
    assert settings.root_path == "/kicadLibrary"
    assert settings.origin == "https://sameisky.tech"
    assert settings.panel_url == "https://sameisky.tech/kicadLibrary/panel"


def test_metadata_urls_include_the_prefix(prefixed_client):
    body = prefixed_client.get("/.well-known/kicad-remote-provider").json()
    assert body["panel_url"] == "https://sameisky.tech/kicadLibrary/panel"
    assert body["api_base_url"] == "https://sameisky.tech/kicadLibrary/api/v1"


def test_panel_assets_are_prefixed(prefixed_client):
    """The live bug: bare /static/... resolves to the site root, not the app."""
    html = prefixed_client.get("/panel").text
    assert '"/kicadLibrary/static/style.css"' in html
    assert '"/kicadLibrary/static/kicad-bridge.js"' in html
    assert 'href="/static/' not in html
    assert 'src="/static/' not in html


@pytest.mark.parametrize(
    "candidate,expected",
    [
        ("https://sameisky.tech/kicadLibrary/panel", True),
        ("https://sameisky.tech/kicadLibrary", True),
        ("https://sameisky.tech/other-app/panel", False),
        ("https://sameisky.tech/kicadLibraryEvil/panel", False),
        ("https://evil.example.net/kicadLibrary/panel", False),
        ("http://sameisky.tech/kicadLibrary/panel", False),
    ],
)
def test_internal_url_check(candidate, expected):
    assert _is_internal_url(candidate, PREFIXED) is expected


def test_internal_url_check_without_prefix():
    assert _is_internal_url("https://host/panel", "https://host") is True
    assert _is_internal_url("https://other/panel", "https://host") is False

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
    assert '"/kicadLibrary/static/css/app.css"' in html
    assert '"/kicadLibrary/static/js/kicad-bridge.js"' in html
    assert '"/kicadLibrary/static/js/panel.js"' in html
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


# --- Root-mounted deployment (https://kicad.sameisky.tech) -------------------


@pytest.fixture()
def root_mounted_client(build_client):
    return build_client(PUBLIC_URL="https://kicad.sameisky.tech")


def test_root_mount_has_empty_prefix(root_mounted_client):
    from app.config import get_settings

    settings = get_settings()
    assert settings.root_path == ""
    assert settings.origin == "https://kicad.sameisky.tech"
    assert settings.panel_url == "https://kicad.sameisky.tech/panel"
    assert settings.session_bootstrap_url == "https://kicad.sameisky.tech/api/v1/session/bootstrap"


def test_root_mount_emits_bare_asset_paths(root_mounted_client):
    """With no prefix the templates must not emit a doubled or dangling slash."""
    html = root_mounted_client.get("/panel").text
    assert 'href="/static/css/app.css"' in html
    assert 'src="/static/js/kicad-bridge.js"' in html
    assert "//static/" not in html


def test_root_mount_cookie_is_secure_on_https(root_mounted_client):
    from app.config import get_settings

    assert get_settings().cookie_secure is True


# --- Browser sign-in behind the prefix ---------------------------------------


def test_the_callback_url_carries_the_prefix(prefixed_client):
    """The provider sends the browser back to a URL it was told; without the
    prefix that lands on whatever else is mounted at the site root."""
    from app.config import get_settings

    assert get_settings().web_callback_url == "https://sameisky.tech/kicadLibrary/auth/callback"


def test_a_bare_next_path_is_resolved_under_the_prefix(prefixed_client):
    """The page that was refused knows its own path, not the origin a proxy
    presents it under, so it sends "/ipn/X" and we put the prefix back."""
    from app.auth.routes import _safe_next
    from app.config import get_settings

    settings = get_settings()

    assert _safe_next("/ipn/1102-0001", settings) == (
        "https://sameisky.tech/kicadLibrary/ipn/1102-0001"
    )
    # Already carrying it, as the signed-out page emits.
    assert _safe_next("/kicadLibrary/ipn/1102-0001", settings) == (
        "https://sameisky.tech/kicadLibrary/ipn/1102-0001"
    )


def test_a_bare_next_path_on_a_root_mount(root_mounted_client):
    from app.auth.routes import _safe_next
    from app.config import get_settings

    assert _safe_next("/ipn/1102-0001", get_settings()) == (
        "https://kicad.sameisky.tech/ipn/1102-0001"
    )


def test_a_next_escaping_the_prefix_falls_back_to_the_panel(prefixed_client):
    """Another app on the same host shares the origin but not the prefix."""
    from app.auth.routes import _safe_next
    from app.config import get_settings

    settings = get_settings()
    assert _safe_next("https://sameisky.tech/other-app/steal", settings) == settings.panel_url

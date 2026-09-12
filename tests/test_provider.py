"""The discovery document must satisfy KiCad's v1 metadata schema."""

REQUIRED = {
    "provider_name",
    "provider_version",
    "api_base_url",
    "panel_url",
    "auth",
    "capabilities",
    "max_download_bytes",
    "supported_asset_types",
}

ALLOWED = REQUIRED | {
    "session_bootstrap_url",
    "allow_insecure_localhost",
    "parts",
    "documentation_url",
    "terms_url",
}

ASSET_TYPES = {"symbol", "footprint", "3dmodel", "spice"}


def test_metadata_has_required_fields_and_no_extras(client):
    body = client.get("/.well-known/kicad-remote-provider").json()
    assert REQUIRED <= set(body)
    assert set(body) <= ALLOWED, f"schema sets additionalProperties:false; extras: {set(body) - ALLOWED}"


def test_oauth2_block_is_complete(client):
    auth = client.get("/.well-known/kicad-remote-provider").json()["auth"]
    assert auth["type"] == "oauth2"
    # The schema conditionally requires both of these when type is oauth2.
    assert auth["metadata_url"]
    assert auth["client_id"]
    assert set(auth) <= {"type", "metadata_url", "client_id", "scopes"}


def test_capabilities_and_asset_types(client):
    body = client.get("/.well-known/kicad-remote-provider").json()
    assert body["capabilities"]["web_ui_v1"] is True
    assert set(body["supported_asset_types"]) <= ASSET_TYPES
    assert body["supported_asset_types"]
    assert isinstance(body["max_download_bytes"], int) and body["max_download_bytes"] >= 1


def test_bootstrap_url_shares_origin_with_panel(client):
    """KiCad rejects a bootstrap URL whose origin differs from the panel."""
    from urllib.parse import urlsplit

    body = client.get("/.well-known/kicad-remote-provider").json()
    panel = urlsplit(body["panel_url"])
    bootstrap = urlsplit(body["session_bootstrap_url"])
    assert (panel.scheme, panel.netloc) == (bootstrap.scheme, bootstrap.netloc)

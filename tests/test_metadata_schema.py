"""Validate the discovery document against KiCad's own published schema.

The fixture is a verbatim copy of resources/schemas/ from the KiCad source tree.
Refresh it when targeting a newer KiCad release -- a drift here shows up in
production as a provider KiCad silently refuses to load.
"""

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads(
    (Path(__file__).parent / "fixtures" / "kicad-remote-provider-metadata-v1.schema.json").read_text()
)


def test_document_validates_against_kicad_schema(client):
    body = client.get("/.well-known/kicad-remote-provider").json()
    jsonschema.validate(instance=body, schema=SCHEMA)


def test_schema_rejects_oauth2_without_client_id():
    """Guards the fixture itself: this rule is why auth config is all-or-nothing."""
    bad = {
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
        jsonschema.validate(instance=bad, schema=SCHEMA)

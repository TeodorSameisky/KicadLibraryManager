"""The remote-provider discovery document.

KiCad fetches this first and validates it against its own schema,
kicad-remote-provider-metadata-v1. That schema sets additionalProperties to
false, so one unrecognised key fails the whole document: an optional field is
only emitted when it is implemented.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.config import Settings
from app.web.deps import get_settings

router = APIRouter(tags=["provider"])


@router.get("/.well-known/kicad-remote-provider")
async def provider_metadata(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    auth: dict[str, Any] = {"type": "none"}
    if settings.auth_configured:
        auth = {
            "type": "oauth2",
            "metadata_url": settings.oidc_metadata_url,
            "client_id": settings.oidc_client_id,
        }
        if settings.oidc_scopes:
            auth["scopes"] = list(settings.oidc_scopes)

    document: dict[str, Any] = {
        "provider_name": settings.provider_name,
        "provider_version": settings.provider_version,
        "api_base_url": settings.api_base_url,
        "panel_url": settings.panel_url,
        "auth": auth,
        "capabilities": {
            "web_ui_v1": True,
            "parts_v1": settings.capability_parts,
            # KiCad rejects a provider with neither of these, though its
            # schema allows it.
            "direct_downloads_v1": settings.capability_direct_downloads,
            "inline_payloads_v1": settings.capability_inline_payloads,
        },
        "max_download_bytes": settings.max_download_bytes,
        "supported_asset_types": list(settings.supported_asset_types),
    }

    # Only advertised when it can do something. KiCad requires it to share an
    # origin with the panel, which deriving both from PUBLIC_URL guarantees.
    if settings.auth_configured:
        document["session_bootstrap_url"] = settings.session_bootstrap_url
    if settings.allow_insecure_localhost:
        document["allow_insecure_localhost"] = True

    return document

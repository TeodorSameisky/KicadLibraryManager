"""The remote-provider discovery document.

KiCad fetches this first and validates it against
kicad-remote-provider-metadata-v1.schema.json. That schema sets
additionalProperties:false, so an unrecognised key fails the whole document --
only emit optional fields we actually implement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter()


@router.get("/.well-known/kicad-remote-provider")
async def provider_metadata(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    if settings.auth_configured:
        auth: dict[str, object] = {
            "type": "oauth2",
            "metadata_url": settings.oidc_metadata_url,
            "client_id": settings.oidc_client_id,
        }
        if settings.oidc_scopes:
            auth["scopes"] = list(settings.oidc_scopes)
    else:
        auth = {"type": "none"}

    metadata: dict[str, object] = {
        "provider_name": settings.provider_name,
        "provider_version": settings.provider_version,
        "api_base_url": settings.api_base_url,
        "panel_url": settings.panel_url,
        "auth": auth,
        "capabilities": {
            "web_ui_v1": True,
            "parts_v1": settings.capability_parts,
            "direct_downloads_v1": settings.capability_direct_downloads,
            "inline_payloads_v1": settings.capability_inline_payloads,
        },
        "max_download_bytes": settings.max_download_bytes,
        "supported_asset_types": list(settings.supported_asset_types),
    }

    # Only advertise the bootstrap endpoint when it can actually do something;
    # KiCad requires it to share an origin with panel_url.
    if settings.auth_configured:
        metadata["session_bootstrap_url"] = settings.session_bootstrap_url

    if settings.allow_insecure_localhost:
        metadata["allow_insecure_localhost"] = True

    return metadata

"""The remote-provider discovery document.

KiCad fetches this first and validates it against its own schema,
kicad-remote-provider-metadata-v1. That schema sets additionalProperties to
false, so one unrecognised key fails the whole document: an optional field is
only emitted when it is implemented.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings
from app.web.deps import get_settings
from app.web.schemas.system import ProviderMetadata

router = APIRouter(tags=["provider"])


# Without what is None: KiCad's schema allows no key it does not know, and an
# optional one is only sent when it is implemented.
@router.get("/.well-known/kicad-remote-provider", response_model_exclude_none=True)
async def provider_metadata(settings: Settings = Depends(get_settings)) -> ProviderMetadata:
    return ProviderMetadata.of(settings)

"""Health, the session, and the document KiCad reads first."""

from __future__ import annotations

from pydantic import BaseModel

from app.auth.sessions import Session
from app.config import Settings
from app.library.service import LibraryService


class Health(BaseModel):
    status: str
    version: str


class Readiness(BaseModel):
    """Whether there is a catalog to serve."""

    status: str
    parts: int
    sources: int
    error: str | None

    @classmethod
    def of(cls, library: LibraryService) -> Readiness:
        snapshot = library.snapshot
        return cls(
            status=library.state.value,
            parts=snapshot.part_count,
            sources=len(snapshot.sources),
            error=library.error,
        )


class Me(BaseModel):
    """The current session. With nobody signed in, only `authenticated`."""

    authenticated: bool
    subject: str | None = None
    display_name: str | None = None

    @classmethod
    def of(cls, session: Session | None) -> Me:
        if session is None:
            return cls(authenticated=False)
        return cls(authenticated=True, subject=session.subject, display_name=session.display_name)


class WebhookAccepted(BaseModel):
    status: str


# -- KiCad's discovery document -------------------------------------------------
#
# KiCad validates it against its own schema, which allows no key it does not
# know. An optional field is therefore left out when it has nothing to say,
# not sent as null: the route that answers with this drops what is None.


class ProviderAuth(BaseModel):
    type: str  # "none", or "oauth2" with the rest
    metadata_url: str | None = None
    client_id: str | None = None
    scopes: list[str] | None = None


class ProviderCapabilities(BaseModel):
    web_ui_v1: bool
    parts_v1: bool
    # KiCad rejects a provider with neither of these, though its schema
    # allows it.
    direct_downloads_v1: bool
    inline_payloads_v1: bool


class ProviderMetadata(BaseModel):
    provider_name: str
    provider_version: str
    api_base_url: str
    panel_url: str
    auth: ProviderAuth
    capabilities: ProviderCapabilities
    max_download_bytes: int
    supported_asset_types: list[str]
    # Only advertised when it can do something. KiCad requires it to share an
    # origin with the panel, which deriving both from PUBLIC_URL guarantees.
    session_bootstrap_url: str | None = None
    allow_insecure_localhost: bool | None = None

    @classmethod
    def of(cls, settings: Settings) -> ProviderMetadata:
        auth = ProviderAuth(type="none")
        if settings.auth_configured:
            auth = ProviderAuth(
                type="oauth2",
                metadata_url=settings.oidc_metadata_url,
                client_id=settings.oidc_client_id,
                scopes=list(settings.oidc_scopes) or None,
            )
        return cls(
            provider_name=settings.provider_name,
            provider_version=settings.provider_version,
            api_base_url=settings.api_base_url,
            panel_url=settings.panel_url,
            auth=auth,
            capabilities=ProviderCapabilities(
                web_ui_v1=True,
                parts_v1=settings.capability_parts,
                direct_downloads_v1=settings.capability_direct_downloads,
                inline_payloads_v1=settings.capability_inline_payloads,
            ),
            max_download_bytes=settings.max_download_bytes,
            supported_asset_types=list(settings.supported_asset_types),
            session_bootstrap_url=(
                settings.session_bootstrap_url if settings.auth_configured else None
            ),
            allow_insecure_localhost=True if settings.allow_insecure_localhost else None,
        )

"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.strip() else default


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Runtime settings.

    PUBLIC_URL must be the externally reachable origin: KiCad validates that the
    panel, bootstrap and nonce URLs share an origin, so a wrong value here fails
    the handshake rather than degrading gracefully.
    """

    public_url: str
    provider_name: str
    provider_version: str

    auth_enabled: bool
    oidc_metadata_url: str | None
    oidc_client_id: str | None
    oidc_scopes: tuple[str, ...]
    oidc_audience: str | None

    nonce_ttl_seconds: int
    session_ttl_seconds: int
    cookie_name: str
    cookie_secure: bool

    hsts_enabled: bool
    hsts_max_age: int
    hsts_include_subdomains: bool
    hsts_preload: bool

    allow_insecure_localhost: bool
    max_download_bytes: int
    supported_asset_types: tuple[str, ...]

    capability_parts: bool
    capability_direct_downloads: bool
    capability_inline_payloads: bool

    @property
    def root_path(self) -> str:
        """Path prefix this app is mounted under, e.g. "/kicadLibrary".

        A reverse proxy that strips the prefix leaves the app seeing "/panel"
        while the browser sits at "/kicadLibrary/panel", so every URL we emit
        has to carry the prefix back or it resolves against the site root.
        """
        return urlsplit(self.public_url).path.rstrip("/")

    @property
    def origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def api_base_url(self) -> str:
        return f"{self.public_url}/api/v1"

    @property
    def panel_url(self) -> str:
        return f"{self.public_url}/panel"

    @property
    def session_bootstrap_url(self) -> str:
        return f"{self.api_base_url}/session/bootstrap"

    @property
    def auth_configured(self) -> bool:
        return bool(self.auth_enabled and self.oidc_metadata_url and self.oidc_client_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    public_url = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return Settings(
        public_url=public_url,
        provider_name=os.environ.get("PROVIDER_NAME", "KiCad Library Manager"),
        provider_version=os.environ.get("PROVIDER_VERSION", "0.1.0"),
        auth_enabled=_bool("AUTH_ENABLED", False),
        oidc_metadata_url=os.environ.get("OIDC_METADATA_URL") or None,
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID") or None,
        oidc_scopes=_csv("OIDC_SCOPES", ("openid", "profile", "email")),
        oidc_audience=os.environ.get("OIDC_AUDIENCE") or None,
        nonce_ttl_seconds=_int("NONCE_TTL_SECONDS", 120),
        session_ttl_seconds=_int("SESSION_TTL_SECONDS", 8 * 60 * 60),
        cookie_name=os.environ.get("SESSION_COOKIE_NAME", "klm_session"),
        cookie_secure=_bool("COOKIE_SECURE", public_url.startswith("https://")),
        hsts_enabled=_bool("HSTS_ENABLED", True),
        hsts_max_age=_int("HSTS_MAX_AGE", 31536000),
        hsts_include_subdomains=_bool("HSTS_INCLUDE_SUBDOMAINS", False),
        # Preload is a one-way door: browsers ship the entry and removal takes
        # months, so it stays opt-in.
        hsts_preload=_bool("HSTS_PRELOAD", False),
        allow_insecure_localhost=_bool("ALLOW_INSECURE_LOCALHOST", True),
        max_download_bytes=_int("MAX_DOWNLOAD_BYTES", 64 * 1024 * 1024),
        supported_asset_types=_csv("SUPPORTED_ASSET_TYPES", ("symbol", "footprint", "3dmodel")),
        capability_parts=_bool("CAPABILITY_PARTS", False),
        # KiCad refuses to register a provider unless at least one asset
        # transport is advertised, so these default on even before the catalog
        # can serve anything.
        capability_direct_downloads=_bool("CAPABILITY_DIRECT_DOWNLOADS", True),
        capability_inline_payloads=_bool("CAPABILITY_INLINE_PAYLOADS", True),
    )

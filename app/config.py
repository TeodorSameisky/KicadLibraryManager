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


class ConfigError(Exception):
    """Configuration that cannot produce a working deployment."""


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

    library_sources: str
    library_workdir: str
    remote_library_prefix: str

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

    def problems(self) -> list[str]:
        """Misconfigurations worth reporting at startup.

        Each of these otherwise surfaces much later as a puzzle: KiCad loading
        a panel on the user's own machine, or a login that drops its session
        with nothing in the logs to say why.
        """
        found: list[str] = []
        parts = urlsplit(self.public_url)

        if parts.scheme not in ("http", "https") or not parts.netloc:
            found.append(
                f"PUBLIC_URL must be an absolute URL; got {self.public_url!r}")
        elif parts.hostname in ("localhost", "127.0.0.1", "::1"):
            found.append(
                "PUBLIC_URL points at localhost, so KiCad would load the panel "
                "from each user's own machine; set it to the deployed address")

        if self.auth_enabled and not self.auth_configured:
            missing = [
                name for name, value in (
                    ("OIDC_METADATA_URL", self.oidc_metadata_url),
                    ("OIDC_CLIENT_ID", self.oidc_client_id),
                )
                if not value
            ]
            found.append(
                f"AUTH_ENABLED is on but {', '.join(missing)} is not set")

        if self.auth_configured and parts.scheme != "https":
            found.append(
                "OAuth2 requires https: Secure session cookies are dropped over "
                "http, which presents as a login that silently does nothing")

        if self.cookie_secure and parts.scheme != "https":
            found.append(
                "COOKIE_SECURE is on but PUBLIC_URL is http, so the session "
                "cookie will never be sent back")

        if not (self.capability_direct_downloads or self.capability_inline_payloads):
            found.append(
                "KiCad refuses a provider that advertises neither "
                "CAPABILITY_DIRECT_DOWNLOADS nor CAPABILITY_INLINE_PAYLOADS")

        return found


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
        # "profile" is deliberately absent: authentik's profile scope carries the
        # user's group list, and KiCad persists its token bundle in the OS
        # credential store -- capped at 2560 bytes (~1280 UTF-16 chars) on
        # Windows. Oversized tokens fail with "Failed to store remote provider
        # tokens securely" after an otherwise successful login.
        oidc_scopes=_csv("OIDC_SCOPES", ("openid", "email")),
        oidc_audience=os.environ.get("OIDC_AUDIENCE") or None,
        nonce_ttl_seconds=_int("NONCE_TTL_SECONDS", 120),
        session_ttl_seconds=_int("SESSION_TTL_SECONDS", 8 * 60 * 60),
        cookie_name=os.environ.get("SESSION_COOKIE_NAME", "klm_session"),
        cookie_secure=_bool("COOKIE_SECURE", public_url.startswith("https://")),
        library_sources=os.environ.get("LIBRARY_SOURCES", ""),
        library_workdir=os.environ.get("LIBRARY_WORKDIR", "/data/sources"),
        remote_library_prefix=os.environ.get("REMOTE_LIBRARY_PREFIX", "remote"),
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

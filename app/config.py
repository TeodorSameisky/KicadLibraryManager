"""Configuration, read once from the environment.

Nothing here raises. A container that refuses to start cannot be inspected,
and every one of these mistakes can be fixed from the deployment's own
settings page while the app keeps serving what it can. So a bad value falls
back to its default and is recorded, and `Settings.problems()` is what the
log and the status page show.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from app import __version__
from app.library.placement import PlacementOptions
from app.library.sources import Source, SourceError, parse_sources

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _Environment:
    """Typed reads of environment variables, remembering what was malformed."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ
        self.errors: list[str] = []

    def text(self, name: str, default: str = "") -> str:
        return (self._environ.get(name) or "").strip() or default

    def optional(self, name: str) -> str | None:
        return self.text(name) or None

    def flag(self, name: str, default: bool) -> bool:
        raw = self.text(name).lower()
        if not raw:
            return default
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
        self.errors.append(f"{name} must be true or false; got {raw!r}, using {default}")
        return default

    def integer(self, name: str, default: int) -> int:
        raw = self.text(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            self.errors.append(f"{name} must be a whole number; got {raw!r}, using {default}")
            return default

    def words(self, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        """A comma-separated list."""
        found = tuple(word.strip() for word in self.text(name).split(",") if word.strip())
        return found or default


@dataclass(frozen=True)
class Settings:
    # The externally reachable address, including any path prefix a reverse
    # proxy mounts the app under. Every URL the app hands out is derived from
    # it, and KiCad checks that they share an origin, so a wrong value fails
    # the handshake outright instead of degrading.
    public_url: str = "http://localhost:8000"
    provider_name: str = "KiCad Library Manager"
    provider_version: str = __version__

    # -- who may read the library
    auth_enabled: bool = False
    oidc_metadata_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None  # only for a confidential client
    oidc_audience: str | None = None
    # "profile" is deliberately not requested. KiCad keeps its tokens in the
    # OS credential store, which on Windows holds 2560 bytes -- about 1280
    # characters. A provider that packs names and groups into the tokens goes
    # over, and the login fails after succeeding with "Failed to store remote
    # provider tokens securely".
    oidc_scopes: tuple[str, ...] = ("openid", "email")
    # Sign-in from an ordinary browser, with this app as the OAuth2 client.
    web_login_enabled: bool = True
    # Who may change the library and delete records: usernames, e-mail
    # addresses or subject ids, as the identity provider reports them.
    librarians: tuple[str, ...] = ()

    # -- sessions
    nonce_ttl_seconds: int = 120
    # A browser sign-in can take a password, a consent screen and a second
    # factor, none of which fit in the two minutes a nonce gets.
    login_ttl_seconds: int = 600
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_name: str = "klm_session"
    cookie_secure: bool = False

    # -- response headers
    hsts_enabled: bool = True
    hsts_max_age: int = 31_536_000
    hsts_include_subdomains: bool = False
    # A one-way door: browsers ship the preload list and leaving it takes
    # months. Never on unless asked for.
    hsts_preload: bool = False

    # -- the library
    library_sources: tuple[Source, ...] = ()
    library_workdir: str = "/data/sources"
    remote_library_prefix: str = "remote"
    # How often to look whether a source has new commits, when nothing has
    # said so. Zero never looks: a restart, a librarian or a webhook has to.
    library_refresh_minutes: int = 15
    # What the git host signs its webhook calls with. Unset, there is no webhook.
    webhook_secret: str | None = None
    # A token that may push to the library's repository. Without one the app
    # reads the library and cannot add to it.
    library_write_token: str | None = None

    # -- bills of materials
    # One SQLite file. It is the only thing here that cannot be rebuilt from
    # somewhere else, so it is the thing to back up.
    database_path: str = "/data/library-manager.db"

    # -- what the discovery document advertises
    allow_insecure_localhost: bool = True
    max_download_bytes: int = 64 * 1024 * 1024
    supported_asset_types: tuple[str, ...] = ("symbol", "footprint", "3dmodel")
    capability_parts: bool = False
    # KiCad refuses to register a provider that offers neither transport, so
    # both default on.
    capability_direct_downloads: bool = True
    capability_inline_payloads: bool = True

    # -- logging
    log_level: str = "INFO"
    log_json: bool = False

    # Values that could not be read, each already replaced by its default.
    errors: tuple[str, ...] = ()

    @staticmethod
    def from_env(environ: Mapping[str, str] | None = None) -> Settings:
        env = _Environment(os.environ if environ is None else environ)
        public_url = env.text("PUBLIC_URL", "http://localhost:8000").rstrip("/")

        try:
            sources = parse_sources(env.text("LIBRARY_SOURCES"))
        except SourceError as exc:
            sources = ()
            env.errors.append(f"LIBRARY_SOURCES is unusable, so no library is loaded: {exc}")

        return Settings(
            public_url=public_url,
            provider_name=env.text("PROVIDER_NAME", "KiCad Library Manager"),
            provider_version=env.text("PROVIDER_VERSION", __version__),
            auth_enabled=env.flag("AUTH_ENABLED", False),
            oidc_metadata_url=env.optional("OIDC_METADATA_URL"),
            oidc_client_id=env.optional("OIDC_CLIENT_ID"),
            oidc_client_secret=env.optional("OIDC_CLIENT_SECRET"),
            oidc_audience=env.optional("OIDC_AUDIENCE"),
            oidc_scopes=env.words("OIDC_SCOPES", ("openid", "email")),
            web_login_enabled=env.flag("WEB_LOGIN_ENABLED", True),
            librarians=env.words("LIBRARIANS", ()),
            nonce_ttl_seconds=env.integer("NONCE_TTL_SECONDS", 120),
            login_ttl_seconds=env.integer("LOGIN_TTL_SECONDS", 600),
            session_ttl_seconds=env.integer("SESSION_TTL_SECONDS", 8 * 60 * 60),
            cookie_name=env.text("SESSION_COOKIE_NAME", "klm_session"),
            cookie_secure=env.flag("COOKIE_SECURE", public_url.startswith("https://")),
            hsts_enabled=env.flag("HSTS_ENABLED", True),
            hsts_max_age=env.integer("HSTS_MAX_AGE", 31_536_000),
            hsts_include_subdomains=env.flag("HSTS_INCLUDE_SUBDOMAINS", False),
            hsts_preload=env.flag("HSTS_PRELOAD", False),
            library_sources=sources,
            library_workdir=env.text("LIBRARY_WORKDIR", "/data/sources"),
            remote_library_prefix=env.text("REMOTE_LIBRARY_PREFIX", "remote"),
            library_refresh_minutes=max(0, env.integer("LIBRARY_REFRESH_MINUTES", 15)),
            webhook_secret=env.optional("WEBHOOK_SECRET"),
            library_write_token=env.optional("LIBRARY_WRITE_TOKEN"),
            database_path=env.text("DATABASE_PATH", "/data/library-manager.db"),
            allow_insecure_localhost=env.flag("ALLOW_INSECURE_LOCALHOST", True),
            max_download_bytes=env.integer("MAX_DOWNLOAD_BYTES", 64 * 1024 * 1024),
            supported_asset_types=env.words(
                "SUPPORTED_ASSET_TYPES", ("symbol", "footprint", "3dmodel")
            ),
            capability_parts=env.flag("CAPABILITY_PARTS", False),
            capability_direct_downloads=env.flag("CAPABILITY_DIRECT_DOWNLOADS", True),
            capability_inline_payloads=env.flag("CAPABILITY_INLINE_PAYLOADS", True),
            log_level=env.text("LOG_LEVEL", "INFO").upper(),
            log_json=env.text("LOG_FORMAT", "text").lower() == "json",
            errors=tuple(env.errors),
        )

    # -- addresses

    @property
    def origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def hostname(self) -> str:
        return urlsplit(self.public_url).hostname or "localhost"

    @property
    def root_path(self) -> str:
        """The path prefix the app is mounted under, e.g. "/kicadLibrary".

        A proxy that strips the prefix leaves the app seeing "/panel" while
        the browser sits at "/kicadLibrary/panel". Every URL sent out has to
        carry the prefix back, or it resolves against the site root.
        """
        return urlsplit(self.public_url).path.rstrip("/")

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
    def web_callback_url(self) -> str:
        """Where the identity provider returns a browser after sign-in.

        Fixed, unlike KiCad's loopback address with its ephemeral port, so it
        has to be registered with the provider as an allowed redirect URI.
        """
        return f"{self.public_url}/auth/callback"

    # -- what is switched on

    @property
    def auth_configured(self) -> bool:
        """Whether the library is closed to anyone without a session."""
        return bool(self.auth_enabled and self.oidc_metadata_url and self.oidc_client_id)

    @property
    def web_login_configured(self) -> bool:
        return self.auth_configured and self.web_login_enabled

    @property
    def placement(self) -> PlacementOptions:
        return PlacementOptions(
            public_url=self.public_url, remote_prefix=self.remote_library_prefix
        )

    # -- what is wrong

    def problems(self) -> list[str]:
        """Misconfigurations worth putting in front of whoever can fix them.

        Each of these otherwise surfaces much later as a puzzle: KiCad loading
        a panel from the user's own machine, or a login that drops its session
        with nothing in the logs to say why.
        """
        found = list(self.errors)
        url = urlsplit(self.public_url)
        https = url.scheme == "https"

        if url.scheme not in ("http", "https") or not url.netloc:
            found.append(f"PUBLIC_URL must be an absolute URL; got {self.public_url!r}")
        elif url.hostname in _LOCAL_HOSTS:
            found.append(
                "PUBLIC_URL points at localhost, so KiCad would load the panel from "
                "each user's own machine; set it to the address this is deployed at"
            )

        if self.auth_enabled and not self.auth_configured:
            missing = [
                name
                for name, value in (
                    ("OIDC_METADATA_URL", self.oidc_metadata_url),
                    ("OIDC_CLIENT_ID", self.oidc_client_id),
                )
                if not value
            ]
            found.append(
                f"AUTH_ENABLED is on but {' and '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not set, so the library is open"
            )

        if self.auth_configured and not self.librarians:
            found.append(
                "LIBRARIANS is empty, so nobody can refresh the library or delete a BOM; "
                "list the usernames of the people who look after it"
            )

        if self.auth_configured and not https:
            found.append(
                "sign-in needs https: over http the session cookie is dropped, "
                "which looks like a login that does nothing"
            )
        elif self.cookie_secure and not https:
            found.append(
                "COOKIE_SECURE is on but PUBLIC_URL is http, so the session cookie "
                "is never sent back"
            )

        if not (self.capability_direct_downloads or self.capability_inline_payloads):
            found.append(
                "KiCad refuses a provider that advertises neither "
                "CAPABILITY_DIRECT_DOWNLOADS nor CAPABILITY_INLINE_PAYLOADS"
            )

        return found

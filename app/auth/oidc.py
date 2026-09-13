"""Talking to an OIDC-compliant identity provider.

There are two ways a session starts here, because there are two clients.

KiCad runs Authorization Code + PKCE itself and hands us the resulting access
token at the bootstrap endpoint; for that flow we only decide whether a
presented token is valid and who it belongs to. Two token shapes are supported,
because providers differ:

* JWT access tokens (Keycloak, Authentik, Auth0, Zitadel) are verified locally
  against the provider's JWKS.
* Opaque access tokens (Google) cannot be verified locally, so we fall back to
  calling the provider's userinfo endpoint.

A browser arriving at a part page has no KiCad to do that for it -- the link
lives in a placed symbol and may be followed from a PDF export years later --
so for that flow this app is the OAuth2 client and runs the exchange itself.
Either way we never see the user's credentials.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.auth.session import friendly_name
from app.config import Settings

_METADATA_TTL_SECONDS = 3600

# RFC 7636 allows 43-128 characters from an unreserved set; token_urlsafe emits
# exactly that alphabet.
_VERIFIER_BYTES = 64


def make_code_verifier() -> str:
    return secrets.token_urlsafe(_VERIFIER_BYTES)[:128]


def code_challenge_for(verifier: str) -> str:
    """The S256 challenge. `plain` is allowed by the RFC and worth nothing."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _has_display_claim(claims: dict[str, Any]) -> bool:
    return any(claims.get(key) for key in ("name", "preferred_username", "email"))


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or untrusted."""


def _oauth_error(response: httpx.Response) -> str:
    """The provider's own error code, or the status if it sent none."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"

    code = body.get("error")
    if not code:
        return f"HTTP {response.status_code}"
    description = body.get("error_description")
    return f"{code}: {description}" if description else str(code)


@dataclass(frozen=True)
class Principal:
    """An authenticated user."""

    subject: str
    claims: dict[str, Any]

    @property
    def display_name(self) -> str:
        return friendly_name(self.subject, self.claims)


class OidcVerifier:
    """Caches provider metadata and JWKS, and verifies access tokens."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._metadata: dict[str, Any] | None = None
        self._metadata_fetched_at = 0.0
        self._jwk_client: PyJWKClient | None = None

    async def metadata(self) -> dict[str, Any]:
        if not self._settings.oidc_metadata_url:
            raise TokenError("No OIDC metadata URL configured")

        fresh = time.monotonic() - self._metadata_fetched_at < _METADATA_TTL_SECONDS
        if self._metadata is not None and fresh:
            return self._metadata

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self._settings.oidc_metadata_url)
            response.raise_for_status()
            self._metadata = response.json()

        self._metadata_fetched_at = time.monotonic()
        self._jwk_client = None  # keys may have rotated with the metadata
        return self._metadata

    # -- this app as the OAuth2 client ------------------------------------

    async def authorization_url(self, *, redirect_uri: str, state: str, code_challenge: str) -> str:
        """Where to send a browser to sign in."""
        metadata = await self.metadata()
        endpoint = metadata.get("authorization_endpoint")
        if not endpoint:
            raise TokenError("Provider metadata has no authorization_endpoint")

        query = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id or "",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._settings.oidc_scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{urlencode(query)}"

    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> Principal:
        """Redeem an authorization code, returning who it belongs to."""
        metadata = await self.metadata()
        endpoint = metadata.get("token_endpoint")
        if not endpoint:
            raise TokenError("Provider metadata has no token_endpoint")

        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._settings.oidc_client_id or "",
            "code_verifier": code_verifier,
        }
        # Absent for a public client, which is the default: PKCE is what
        # proves the exchange belongs to the request that started it.
        if self._settings.oidc_client_secret:
            form["client_secret"] = self._settings.oidc_client_secret

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                endpoint, data=form, headers={"Accept": "application/json"}
            )

        if response.status_code >= 400:
            # The body names the OAuth2 error ("invalid_grant"), which is the
            # difference between a misconfigured redirect URI and a stale
            # code. The code and verifier are not in it.
            raise TokenError(f"Token exchange failed: {_oauth_error(response)}")

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise TokenError("Token response carried no access_token")

        return await self.verify(str(access_token))

    # -- verifying a token someone else obtained --------------------------

    async def verify(self, token: str) -> Principal:
        if not token or not token.strip():
            raise TokenError("Empty access token")

        metadata = await self.metadata()

        # A JWT has three dot-separated segments; anything else is opaque and can
        # only be resolved by asking the provider.
        if token.count(".") == 2:
            try:
                principal = self._verify_jwt(token, metadata)
            except TokenError:
                raise
            except Exception as exc:  # malformed despite looking like a JWT
                raise TokenError(f"Token verification failed: {exc}") from exc

            # Identity claims usually ride in the id_token, but KiCad only hands
            # us the access token, so the subject is often all we have. userinfo
            # returns whatever the granted scopes allow.
            if not _has_display_claim(principal.claims):
                principal = await self._enrich(token, principal, metadata)
            return principal

        return await self._verify_via_userinfo(token, metadata)

    async def _enrich(
        self, token: str, principal: Principal, metadata: dict[str, Any]
    ) -> Principal:
        """Best-effort: a failure here must not cost the user their login."""
        try:
            enriched = await self._verify_via_userinfo(token, metadata)
        except (TokenError, httpx.HTTPError):
            return principal

        if enriched.subject != principal.subject:
            # Different subject means the response is not about this token.
            return principal

        merged = {**enriched.claims, **principal.claims}
        return Principal(subject=principal.subject, claims=merged)

    def _verify_jwt(self, token: str, metadata: dict[str, Any]) -> Principal:
        jwks_uri = metadata.get("jwks_uri")
        if not jwks_uri:
            raise TokenError("Provider metadata has no jwks_uri")

        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(jwks_uri, cache_keys=True)

        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        except Exception as exc:
            raise TokenError(f"No usable signing key: {exc}") from exc

        audience = self._settings.oidc_audience
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
                issuer=metadata.get("issuer"),
                audience=audience,
                options={
                    "verify_aud": audience is not None,
                    "require": ["exp", "sub"],
                },
            )
        except jwt.PyJWTError as exc:
            raise TokenError(f"Invalid token: {exc}") from exc

        subject = claims.get("sub")
        if not subject:
            raise TokenError("Token has no subject")
        return Principal(subject=str(subject), claims=claims)

    async def _verify_via_userinfo(self, token: str, metadata: dict[str, Any]) -> Principal:
        userinfo_endpoint = metadata.get("userinfo_endpoint")
        if not userinfo_endpoint:
            raise TokenError("Opaque token presented but provider has no userinfo_endpoint")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )

        if response.status_code == 401:
            raise TokenError("Provider rejected the access token")
        if response.status_code >= 400:
            raise TokenError(f"Userinfo lookup failed with HTTP {response.status_code}")

        claims = response.json()
        subject = claims.get("sub")
        if not subject:
            raise TokenError("Userinfo response has no subject")
        return Principal(subject=str(subject), claims=claims)

"""The identity provider, as any OIDC-compliant one presents itself.

Two jobs, one per way in.

For KiCad's handshake, a presented access token is checked and attributed.
Providers differ in what a token is: Keycloak, Authentik, Auth0 and Zitadel
issue JWTs, which are verified here against the provider's published keys;
Google's are opaque, and can only be resolved by asking the provider's
userinfo endpoint whose they are.

For a browser, this app is the OAuth2 client: it builds the address that
starts Authorization Code + PKCE and redeems the code that comes back.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.auth.identity import Principal

# Asymmetric algorithms only. Accepting HS256 would let anyone holding the
# provider's public key sign their own tokens with it.
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

_METADATA_TTL_SECONDS = 3600
# A token signed with a key we have not seen may mean the provider rotated
# its keys, so they are fetched again -- but not for every bad token.
_KEY_REFRESH_INTERVAL_SECONDS = 60
_TIMEOUT_SECONDS = 10.0


class IdentityError(Exception):
    """A token or authorization code was not accepted."""


class ProviderError(Exception):
    """The identity provider could not be reached, or made no sense."""


# -- PKCE (RFC 7636) --------------------------------------------------------


def new_code_verifier() -> str:
    """43 to 128 characters from the unreserved set; this yields 86."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """The S256 challenge. The RFC also allows `plain`, which proves nothing."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# -- the provider -----------------------------------------------------------


class OidcClient:
    def __init__(
        self,
        *,
        metadata_url: str | None,
        client_id: str | None,
        client_secret: str | None = None,
        scopes: tuple[str, ...] = ("openid",),
        audience: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._metadata_url = metadata_url
        self._client_id = client_id or ""
        self._client_secret = client_secret
        self._scopes = scopes
        self._audience = audience
        self._transport = transport

        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._keys: dict[str | None, Any] = {}
        self._keys_at: float | None = None

    # -- talking to it

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=_TIMEOUT_SECONDS
            ) as client:
                return await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(f"the identity provider is unreachable ({exc!r})") from exc

    @staticmethod
    def _json(response: httpx.Response, what: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"the provider's {what} is not JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"the provider's {what} is not a JSON object")
        return body

    async def metadata(self) -> dict[str, Any]:
        """The provider's discovery document, cached for an hour."""
        if not self._metadata_url:
            raise ProviderError("no OIDC metadata URL is configured")

        fresh = time.monotonic() - self._metadata_at < _METADATA_TTL_SECONDS
        if self._metadata is not None and fresh:
            return self._metadata

        response = await self._request("GET", self._metadata_url)
        if response.status_code >= 400:
            raise ProviderError(f"the provider's metadata returned HTTP {response.status_code}")

        self._metadata = self._json(response, "metadata")
        self._metadata_at = time.monotonic()
        self._keys, self._keys_at = {}, None  # may have rotated along with it
        return self._metadata

    async def _endpoint(self, name: str) -> str:
        endpoint = (await self.metadata()).get(name)
        if not endpoint:
            raise ProviderError(f"the provider's metadata has no {name}")
        return str(endpoint)

    # -- checking a token someone else obtained

    async def verify(self, access_token: str) -> Principal:
        """Decide whether a token is genuine, and whose it is."""
        token = access_token.strip()
        if not token:
            raise IdentityError("empty access token")

        # A JWT is three dot-separated segments. Anything else is opaque.
        if token.count(".") != 2:
            return await self._userinfo(token)

        principal = await self._verify_jwt(token)
        if principal.has_name:
            return principal

        # The claims that name a person usually ride in the id_token, and
        # KiCad only passes on the access token. Userinfo returns whatever the
        # granted scopes allow. Not being able to ask must not cost the user
        # a login that has already been verified.
        try:
            described = await self._userinfo(token)
        except (IdentityError, ProviderError):
            return principal
        if described.subject != principal.subject:
            return principal  # an answer about somebody else
        return Principal(principal.subject, {**described.claims, **principal.claims})

    async def _verify_jwt(self, token: str) -> Principal:
        metadata = await self.metadata()
        issuer = metadata.get("issuer")
        if not issuer:
            raise ProviderError("the provider's metadata has no issuer")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityError(f"malformed token: {exc}") from exc

        key = await self._signing_key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=_ALGORITHMS,
                issuer=issuer,
                audience=self._audience,
                options={"verify_aud": self._audience is not None, "require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(f"invalid token: {exc}") from exc

        subject = claims.get("sub")
        if not subject:
            raise IdentityError("token has no subject")
        return Principal(str(subject), claims)

    async def _signing_key(self, key_id: str | None) -> Any:
        if self._keys_at is None:
            await self._load_keys()

        key = self._find_key(key_id)
        if key is None and self._keys_at is not None:
            stale = time.monotonic() - self._keys_at >= _KEY_REFRESH_INTERVAL_SECONDS
            if stale:
                await self._load_keys()
                key = self._find_key(key_id)

        if key is None:
            raise IdentityError("token is signed with a key the provider does not publish")
        return key

    def _find_key(self, key_id: str | None) -> Any:
        if key_id is not None:
            return self._keys.get(key_id)
        # A token that names no key is only unambiguous when there is one.
        return next(iter(self._keys.values())) if len(self._keys) == 1 else None

    async def _load_keys(self) -> None:
        response = await self._request("GET", await self._endpoint("jwks_uri"))
        if response.status_code >= 400:
            raise ProviderError(f"the provider's keys returned HTTP {response.status_code}")

        try:
            published = jwt.PyJWKSet.from_dict(self._json(response, "key set")).keys
        except jwt.PyJWTError as exc:
            raise ProviderError(f"the provider's key set is unusable: {exc}") from exc

        self._keys = {jwk.key_id: jwk.key for jwk in published}
        self._keys_at = time.monotonic()

    async def _userinfo(self, token: str) -> Principal:
        response = await self._request(
            "GET",
            await self._endpoint("userinfo_endpoint"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code in (401, 403):
            raise IdentityError("the provider rejected the access token")
        if response.status_code >= 400:
            raise ProviderError(f"the provider's userinfo returned HTTP {response.status_code}")

        claims = self._json(response, "userinfo")
        subject = claims.get("sub")
        if not subject:
            raise IdentityError("the provider's userinfo names no subject")
        return Principal(str(subject), claims)

    # -- being the OAuth2 client, for a browser

    async def authorization_url(self, *, redirect_uri: str, state: str, challenge: str) -> str:
        """Where to send a browser to sign in."""
        endpoint = await self._endpoint("authorization_endpoint")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self._scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    async def exchange_code(self, *, code: str, verifier: str, redirect_uri: str) -> Principal:
        """Redeem an authorization code for the person it was issued to."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "code_verifier": verifier,
        }
        # Absent for a public client, which is the usual case: PKCE is what
        # proves this exchange belongs to the request that started it.
        if self._client_secret:
            form["client_secret"] = self._client_secret

        response = await self._request(
            "POST",
            await self._endpoint("token_endpoint"),
            data=form,
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            # The provider's own error code is the difference between a
            # redirect URI it does not recognise and a code that went stale.
            # Neither the code nor the verifier is in it.
            raise IdentityError(f"the provider refused the sign-in ({_oauth_error(response)})")

        access_token = self._json(response, "token response").get("access_token")
        if not access_token:
            raise ProviderError("the provider's token response carried no access_token")
        return await self.verify(str(access_token))


def _oauth_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not body.get("error"):
        return f"HTTP {response.status_code}"
    description = body.get("error_description")
    return f"{body['error']}: {description}" if description else str(body["error"])

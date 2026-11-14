"""The identity provider's client, sessions, and where a sign-in may land.

The provider here is a fake only in that it has no network: it signs real
tokens and checks PKCE the way a real one does.
"""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth.identity import Principal
from app.auth.oidc import (
    IdentityError,
    ProviderError,
    code_challenge,
    new_code_verifier,
)
from app.auth.redirects import is_internal, landing_url
from app.auth.sessions import SessionStore

SUBJECT = "5f4673c44a21b4eeae087f0b57533dbb40f2dd1f4b6c2ff81cbdf2a3391072a6"

# -- who someone is ---------------------------------------------------------


@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"name": "Ada Lovelace", "email": "ada@example.com"}, "Ada Lovelace"),
        ({"preferred_username": "ada", "email": "ada@example.com"}, "ada"),
        ({"email": "ada@example.com"}, "ada@example.com"),
        ({}, "user 5f4673c4"),
        ({"name": ""}, "user 5f4673c4"),
    ],
)
def test_a_person_is_shown_by_the_best_name_there_is(claims, expected):
    """Scopes are kept small so KiCad can store its tokens, so there is often
    no name at all -- and sixty-four characters of hash helps nobody."""
    assert Principal(SUBJECT, claims).display_name == expected


def test_a_short_subject_is_shown_whole():
    assert Principal("ada").display_name == "ada"


# -- checking a token -------------------------------------------------------


async def test_a_signed_token_is_accepted(provider):
    principal = await provider.client().verify(provider.access_token("user-1", email="a@b.c"))

    assert principal.subject == "user-1"
    assert principal.display_name == "a@b.c"
    assert "/userinfo" not in provider.paths(), "nothing to ask when the token names them"


@pytest.mark.parametrize(
    "claims,reason",
    [
        ({"exp": int(time.time()) - 60}, "expired"),
        ({"iss": "https://someone-else.example.com"}, "issuer"),
        ({"exp": None}, "exp"),
    ],
    ids=["expired", "wrong-issuer", "no-expiry"],
)
async def test_a_token_that_does_not_check_out_is_refused(provider, claims, reason):
    with pytest.raises(IdentityError, match=reason):
        await provider.client().verify(provider.access_token(**claims))


async def test_a_token_signed_by_someone_else_is_refused(provider):
    forged = provider.access_token(key=ec.generate_private_key(ec.SECP256R1()))

    with pytest.raises(IdentityError, match="invalid token"):
        await provider.client().verify(forged)


async def test_a_token_naming_an_unpublished_key_is_refused(provider):
    client = provider.client()
    await client.verify(provider.access_token())
    provider.key_id = "another-key"  # the provider now signs with a key id we have not seen

    with pytest.raises(IdentityError, match="does not publish"):
        await client.verify(provider.access_token())
    assert provider.paths().count("/jwks") == 1, "bad tokens do not make us hammer the provider"


async def test_an_unsigned_token_is_refused(provider):
    """`alg: none` is the oldest trick there is."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        b'{"iss":"https://idp.example.com","sub":"admin","exp":9999999999}'
    ).rstrip(b"=").decode()  # fmt: skip

    with pytest.raises(IdentityError):
        await provider.client().verify(f"{header}.{body}.")


@pytest.mark.parametrize("token", ["", "   ", "a.b.c"], ids=["empty", "blank", "gibberish"])
async def test_nonsense_is_refused(provider, token):
    with pytest.raises(IdentityError):
        await provider.client().verify(token)


async def test_the_audience_is_checked_when_one_is_configured(provider):
    client = provider.client(audience="kicad-library-manager")

    accepted = await client.verify(provider.access_token(aud="kicad-library-manager"))
    assert accepted.subject == "user-1"
    with pytest.raises(IdentityError, match="[Aa]udience"):
        await client.verify(provider.access_token(aud="some-other-app"))


async def test_an_opaque_token_is_resolved_by_asking_the_provider(provider):
    """Google's tokens mean nothing on their own."""
    token = provider.opaque_token("user-9", name="Grace Hopper")

    principal = await provider.client().verify(token)

    assert (principal.subject, principal.display_name) == ("user-9", "Grace Hopper")
    with pytest.raises(IdentityError, match="rejected"):
        await provider.client().verify("opaque-unknown")


# -- finding a name for a verified token ------------------------------------


async def test_userinfo_supplies_the_name_a_token_lacks(provider):
    """KiCad passes on only the access token, and the claims that name a
    person usually ride in the id_token."""
    token = provider.access_token(SUBJECT)
    provider.userinfo[token] = {"sub": SUBJECT, "preferred_username": "akadmin"}

    principal = await provider.client().verify(token)

    assert principal.display_name == "akadmin"
    assert principal.claims["iss"] == provider.issuer, "the token's own claims are kept"


async def test_a_login_survives_userinfo_failing(provider):
    token = provider.access_token(SUBJECT)  # userinfo will answer 401 for it

    principal = await provider.client().verify(token)

    assert principal.subject == SUBJECT
    assert principal.display_name == "user 5f4673c4"


async def test_userinfo_about_somebody_else_is_ignored(provider):
    token = provider.access_token(SUBJECT)
    provider.userinfo[token] = {"sub": "someone-else", "preferred_username": "attacker"}

    principal = await provider.client().verify(token)

    assert principal.display_name == "user 5f4673c4"


# -- when the provider is not there -----------------------------------------


async def test_an_unreachable_provider_is_not_a_bad_token(provider):
    """One means "sign in again"; the other means "try later"."""
    provider.unreachable = True

    with pytest.raises(ProviderError, match="unreachable"):
        await provider.client().verify(provider.access_token())


async def test_metadata_is_fetched_once(provider):
    client = provider.client()

    await client.verify(provider.access_token())
    await client.verify(provider.access_token())

    assert provider.paths().count("/.well-known/openid-configuration") == 1
    assert provider.paths().count("/jwks") == 1


async def test_a_provider_missing_what_is_needed_says_so(provider):
    del provider.metadata["jwks_uri"]

    with pytest.raises(ProviderError, match="jwks_uri"):
        await provider.client().verify(provider.access_token())
    with pytest.raises(ProviderError, match="no OIDC metadata URL"):
        await provider.client(metadata_url=None).metadata()


# -- being the client, for a browser ----------------------------------------


async def test_the_authorization_address_asks_for_a_code_with_pkce(provider):
    url = await provider.client().authorization_url(
        redirect_uri="https://library.example.com/auth/callback", state="s-1", challenge="c-1"
    )

    assert url.startswith("https://idp.example.com/authorize?")
    assert {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()} == {
        "response_type": "code",
        "client_id": "kicad-library-manager",
        "redirect_uri": "https://library.example.com/auth/callback",
        "scope": "openid email",
        "state": "s-1",
        "code_challenge": "c-1",
        "code_challenge_method": "S256",  # `plain` proves nothing
    }


async def test_a_code_is_redeemed_for_the_person_it_was_issued_to(provider):
    client, verifier = provider.client(), new_code_verifier()
    redirect = "https://library.example.com/auth/callback"
    url = await client.authorization_url(
        redirect_uri=redirect, state="s", challenge=code_challenge(verifier)
    )
    code = provider.approve(url, "user-7", name="Katherine Johnson")

    principal = await client.exchange_code(code=code, verifier=verifier, redirect_uri=redirect)

    assert (principal.subject, principal.display_name) == ("user-7", "Katherine Johnson")
    with pytest.raises(IdentityError, match="invalid_grant: unknown code"):
        await client.exchange_code(code=code, verifier=verifier, redirect_uri=redirect)


async def test_a_code_cannot_be_redeemed_without_the_verifier(provider):
    """Which is the whole of what PKCE is for."""
    client = provider.client()
    redirect = "https://library.example.com/auth/callback"
    url = await client.authorization_url(
        redirect_uri=redirect, state="s", challenge=code_challenge(new_code_verifier())
    )

    with pytest.raises(IdentityError, match="invalid_grant"):
        await client.exchange_code(
            code=provider.approve(url), verifier=new_code_verifier(), redirect_uri=redirect
        )


async def test_a_secret_is_sent_only_by_a_confidential_client(provider):
    redirect = "https://library.example.com/auth/callback"

    async def form_sent(client) -> dict:
        verifier = new_code_verifier()
        url = await client.authorization_url(
            redirect_uri=redirect, state="s", challenge=code_challenge(verifier)
        )
        await client.exchange_code(
            code=provider.approve(url), verifier=verifier, redirect_uri=redirect
        )
        sent = [r for r in provider.requests if r.url.path == "/token"][-1]
        return parse_qs(sent.content.decode())

    assert "client_secret" not in await form_sent(provider.client())
    confidential = await form_sent(provider.client(client_secret="s3cret"))
    assert confidential["client_secret"] == ["s3cret"]


def test_a_verifier_is_what_the_rfc_allows():
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    verifiers = {new_code_verifier() for _ in range(20)}

    assert len(verifiers) == 20
    assert all(43 <= len(v) <= 128 and set(v) <= allowed for v in verifiers)


def test_the_challenge_is_the_unpadded_base64url_sha256():
    expected = base64.urlsafe_b64encode(hashlib.sha256(b"abc").digest()).decode().rstrip("=")
    assert code_challenge("abc") == expected


# -- sessions ---------------------------------------------------------------

ADA = Principal("user-1", {"name": "Ada Lovelace"})


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def sessions(clock):
    return SessionStore(
        nonce_ttl_seconds=120, login_ttl_seconds=600, session_ttl_seconds=3600, clock=clock
    )


def test_a_nonce_becomes_a_session_once(sessions):
    nonce = sessions.mint_nonce(ADA, "https://example.com/panel")

    session_id, next_url = sessions.redeem_nonce(nonce)

    assert next_url == "https://example.com/panel"
    assert sessions.get(session_id).display_name == "Ada Lovelace"
    assert sessions.redeem_nonce(nonce) is None, "a replay must not mint a second session"


def test_a_nonce_expires(sessions, clock):
    nonce = sessions.mint_nonce(ADA, "https://example.com/panel")
    clock.now += 121

    assert sessions.redeem_nonce(nonce) is None


def test_a_pending_login_is_spent_by_finishing_it(sessions, clock):
    state = sessions.begin_login("verifier", "https://example.com/ipn/1")

    pending = sessions.finish_login(state)

    assert (pending.code_verifier, pending.next_url) == ("verifier", "https://example.com/ipn/1")
    assert sessions.finish_login(state) is None
    assert sessions.finish_login("invented") is None

    late = sessions.begin_login("verifier", "https://example.com/ipn/1")
    clock.now += 601
    assert sessions.finish_login(late) is None


def test_a_session_lasts_until_it_expires_or_is_revoked(sessions, clock):
    kept, revoked = sessions.create(ADA), sessions.create(ADA)

    sessions.revoke(revoked)
    assert sessions.get(revoked) is None
    assert sessions.get(kept).subject == "user-1"

    clock.now += 3601
    assert sessions.get(kept) is None


def test_unknown_session_ids_are_nobody(sessions):
    assert sessions.get("invented") is None
    assert sessions.get(None) is None
    assert sessions.get("") is None
    sessions.revoke(None)  # and revoking nothing is not an error


def test_tokens_cannot_be_guessed(sessions):
    tokens = {sessions.create(ADA) for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(token) >= 40 for token in tokens)


def test_pending_logins_cannot_pile_up_without_limit(sessions, monkeypatch):
    """Starting a sign-in needs no credentials, so anyone can start many."""
    monkeypatch.setattr("app.auth.sessions._MAX_ENTRIES", 5)

    states = [sessions.begin_login(f"verifier-{n}", "/") for n in range(8)]

    assert [sessions.finish_login(s) is not None for s in states] == [False] * 3 + [True] * 5


# -- where a sign-in may land -----------------------------------------------

PREFIXED = "https://sameisky.tech/kicadLibrary"
ROOT = "https://library.example.com"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://sameisky.tech/kicadLibrary/panel", True),
        ("https://sameisky.tech/kicadLibrary", True),
        ("https://sameisky.tech/other-app/panel", False),
        ("https://sameisky.tech/kicadLibraryEvil/panel", False),
        ("https://evil.example.net/kicadLibrary/panel", False),
        ("http://sameisky.tech/kicadLibrary/panel", False),
        ("https://sameisky.tech:8443/kicadLibrary/panel", False),
    ],
)
def test_what_counts_as_inside_a_prefixed_deployment(url, expected):
    """Another app on the same host shares the origin, but not the prefix."""
    assert is_internal(url, PREFIXED) is expected


def test_what_counts_as_inside_a_root_deployment():
    assert is_internal("https://library.example.com/anything", ROOT)
    assert not is_internal("https://library.example.com.evil.net/x", ROOT)


@pytest.mark.parametrize(
    "requested",
    [
        None,
        "",
        "https://evil.example.net/steal",
        "//evil.example.net/steal",
        "http://library.example.com/ipn/1",
        "https://library.example.com.evil.net/x",
        "javascript:alert(1)",
        "ipn/1102-0001",
    ],
    ids=["none", "empty", "offsite", "protocol-relative", "http", "lookalike", "script", "bare"],
)
def test_anything_that_is_not_one_of_our_pages_becomes_the_panel(requested):
    """The redirect carries a fresh session. Sent anywhere else, it is handed
    to whoever named the destination."""
    assert landing_url(requested, ROOT) == "https://library.example.com/panel"


def test_a_path_or_an_address_of_our_own_is_kept():
    assert (
        landing_url("/ipn/1102-0001?x=1", ROOT) == "https://library.example.com/ipn/1102-0001?x=1"
    )
    assert landing_url("https://library.example.com/issues", ROOT) == (
        "https://library.example.com/issues"
    )


def test_a_path_is_resolved_under_the_prefix():
    """The page that refused a request knows its own path, not the address a
    proxy presents it under."""
    expected = "https://sameisky.tech/kicadLibrary/ipn/1102-0001"

    assert landing_url("/ipn/1102-0001", PREFIXED) == expected
    assert landing_url("/kicadLibrary/ipn/1102-0001", PREFIXED) == expected, "not doubled"
    assert landing_url("https://sameisky.tech/other-app/steal", PREFIXED) == f"{PREFIXED}/panel"

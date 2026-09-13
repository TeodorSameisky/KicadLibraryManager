"""Identity claims are fetched from userinfo when the access token lacks them.

KiCad only forwards the access token. authentik puts preferred_username in the
id_token, so without this the panel can only ever show the raw subject.
"""

import pytest

from app.auth.oidc import OidcVerifier, Principal, TokenError
from app.config import get_settings

SUBJECT = "5f4673c44a21b4eeae087f0b57533dbb40f2dd1f4b6c2ff81cbdf2a3391072a6"


class FakeVerifier(OidcVerifier):
    """Stubs out signature checking and the network, keeping the real logic."""

    def __init__(self, jwt_claims, userinfo=None, userinfo_error=None):
        super().__init__(get_settings())
        self._jwt_claims = jwt_claims
        self._userinfo = userinfo
        self._userinfo_error = userinfo_error
        self.userinfo_calls = 0

    async def metadata(self):
        return {"issuer": "https://idp", "userinfo_endpoint": "https://idp/userinfo"}

    def _verify_jwt(self, token, metadata):
        return Principal(subject=self._jwt_claims["sub"], claims=dict(self._jwt_claims))

    async def _verify_via_userinfo(self, token, metadata):
        self.userinfo_calls += 1
        if self._userinfo_error:
            raise self._userinfo_error
        return Principal(subject=self._userinfo["sub"], claims=dict(self._userinfo))


TOKEN = "a.b.c"  # three segments so it takes the JWT path


@pytest.mark.asyncio
async def test_userinfo_supplies_the_username():
    v = FakeVerifier(
        jwt_claims={"sub": SUBJECT, "exp": 1},
        userinfo={"sub": SUBJECT, "preferred_username": "akadmin"},
    )
    p = await v.verify(TOKEN)
    assert p.display_name == "akadmin"
    assert v.userinfo_calls == 1


@pytest.mark.asyncio
async def test_userinfo_is_skipped_when_the_token_already_names_the_user():
    v = FakeVerifier(
        jwt_claims={"sub": SUBJECT, "exp": 1, "email": "teo@example.com"},
        userinfo={"sub": SUBJECT, "preferred_username": "akadmin"},
    )
    p = await v.verify(TOKEN)
    assert p.display_name == "teo@example.com"
    assert v.userinfo_calls == 0, "no need for a round trip when the claim is present"


@pytest.mark.asyncio
async def test_login_survives_a_failing_userinfo():
    v = FakeVerifier(
        jwt_claims={"sub": SUBJECT, "exp": 1},
        userinfo_error=TokenError("userinfo down"),
    )
    p = await v.verify(TOKEN)
    assert p.subject == SUBJECT
    assert p.display_name == "user 5f4673c4"


@pytest.mark.asyncio
async def test_mismatched_subject_is_ignored():
    """A userinfo response about a different user must not be merged in."""
    v = FakeVerifier(
        jwt_claims={"sub": SUBJECT, "exp": 1},
        userinfo={"sub": "someone-else", "preferred_username": "attacker"},
    )
    p = await v.verify(TOKEN)
    assert p.subject == SUBJECT
    assert p.display_name == "user 5f4673c4"

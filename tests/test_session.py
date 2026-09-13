"""Nonce redemption and cookie-session behaviour."""

import pytest

from app.auth.session import SessionStore

CLAIMS = {"sub": "user-1", "name": "Ada Lovelace"}


def test_nonce_is_single_use():
    store = SessionStore(nonce_ttl_seconds=60, session_ttl_seconds=60)
    nonce = store.mint_nonce("user-1", CLAIMS, "https://example.com/panel")

    first = store.redeem_nonce(nonce)
    assert first is not None

    assert store.redeem_nonce(nonce) is None, "a replayed nonce must not mint a second session"


def test_expired_nonce_is_rejected():
    store = SessionStore(nonce_ttl_seconds=-1, session_ttl_seconds=60)
    nonce = store.mint_nonce("user-1", CLAIMS, "https://example.com/panel")
    assert store.redeem_nonce(nonce) is None


def test_session_lookup_and_revoke():
    store = SessionStore(nonce_ttl_seconds=60, session_ttl_seconds=60)
    nonce = store.mint_nonce("user-1", CLAIMS, "https://example.com/panel")
    session_id, next_url = store.redeem_nonce(nonce)

    assert next_url == "https://example.com/panel"
    session = store.get(session_id)
    assert session.subject == "user-1"
    assert session.display_name == "Ada Lovelace"

    store.revoke(session_id)
    assert store.get(session_id) is None


def test_expired_session_is_not_returned():
    store = SessionStore(nonce_ttl_seconds=60, session_ttl_seconds=-1)
    nonce = store.mint_nonce("user-1", CLAIMS, "https://example.com/panel")
    session_id, _ = store.redeem_nonce(nonce)
    assert store.get(session_id) is None


def test_unknown_session_id():
    store = SessionStore(nonce_ttl_seconds=60, session_ttl_seconds=60)
    assert store.get("nope") is None
    assert store.get(None) is None


def test_display_name_shortens_an_opaque_subject():
    """With the profile scope dropped there is often no name claim at all."""
    from app.auth.session import friendly_name

    long_sub = "5f4673c44a21b4eeae087f0b57533dbb40f2dd1f4b6c2ff81cbdf2a3391072a6"
    assert friendly_name(long_sub, {}) == "user 5f4673c4"
    assert friendly_name(long_sub, {"email": "teo@example.com"}) == "teo@example.com"
    assert friendly_name(long_sub, {"name": "Ada"}) == "Ada"
    assert friendly_name("short-id", {}) == "short-id"

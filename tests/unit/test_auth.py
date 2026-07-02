"""Unit tests for JWT decoding — static RSA keys, no network."""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from teamrag import auth
from teamrag.auth import (
    InvalidTokenError,
    UserIdentity,
    _clear_jwks_cache,
    decode_token,
    resolve_identity,
)

ISSUER = "http://localhost:8081/realms/teamrag"
AUDIENCE = "teamrag-gateway"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(key, *, groups=("squad-payments",), exp_delta=300, issuer=ISSUER, aud=AUDIENCE, kid=None):
    now = int(time.time())
    payload = {
        "sub": "user-123",
        "preferred_username": "alice",
        "iss": issuer,
        "aud": aud,
        "iat": now,
        "exp": now + exp_delta,
        "groups": list(groups),
    }
    headers = {"kid": kid} if kid else None
    return jwt.encode(payload, key, algorithm="RS256", headers=headers)


def test_decode_valid_token_yields_identity(rsa_key):
    token = _make_token(rsa_key)
    ident = decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)
    assert isinstance(ident, UserIdentity)
    assert ident.sub == "user-123"
    assert ident.username == "alice"
    assert ident.groups == ("squad-payments",)


def test_decode_strips_leading_slash_from_groups(rsa_key):
    token = _make_token(rsa_key, groups=("/squad-payments",))
    ident = decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)
    assert ident.groups == ("squad-payments",)


def test_decode_strips_reserved_tier_groups(rsa_key):
    """`tier-*` is the reserved system-tier namespace (see teamrag.acl); a

    Keycloak group literally named e.g. "tier-1" must not be honored as a
    caller-supplied group, or it would act as a skeleton key granting every
    tier-1 chunk.
    """
    token = _make_token(rsa_key, groups=("tier-1", "squad-payments"))
    ident = decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)
    assert ident.groups == ("squad-payments",)


def test_decode_missing_groups_claim_defaults_empty(rsa_key):
    now = int(time.time())
    token = jwt.encode(
        {"sub": "s", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 60},
        rsa_key,
        algorithm="RS256",
    )
    ident = decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)
    assert ident.groups == ()


def test_decode_expired_token_raises(rsa_key):
    token = _make_token(rsa_key, exp_delta=-60)
    with pytest.raises(InvalidTokenError):
        decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)


def test_decode_wrong_signature_raises(rsa_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _make_token(other)
    with pytest.raises(InvalidTokenError):
        decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)


def test_decode_wrong_issuer_raises(rsa_key):
    token = _make_token(rsa_key, issuer="http://evil.example")
    with pytest.raises(InvalidTokenError):
        decode_token(token, signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)


def test_decode_garbage_raises(rsa_key):
    with pytest.raises(InvalidTokenError):
        decode_token("not-a-jwt", signing_key=rsa_key.public_key(), issuer=ISSUER, audience=AUDIENCE)


# --- resolve_identity / JWKS path (no network: httpx.AsyncClient is faked) ---


class _FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


class _FakeResponse:
    def __init__(self, payload=None, *, invalid_json=False):
        self._payload = payload
        self._invalid_json = invalid_json

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._invalid_json:
            raise ValueError("malformed JWKS body")
        return self._payload


def _patch_jwks(monkeypatch, response: _FakeResponse) -> None:
    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return response

    monkeypatch.setattr(auth.httpx, "AsyncClient", _FakeAsyncClient)


def _jwk_for(public_key, kid: str) -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


@pytest.fixture()
def enabled_auth(monkeypatch):
    from teamrag.config import settings

    monkeypatch.setattr(settings, "OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", AUDIENCE)
    _clear_jwks_cache()
    yield
    _clear_jwks_cache()


async def test_resolve_identity_none_when_auth_disabled(monkeypatch):
    from teamrag.config import settings

    monkeypatch.setattr(settings, "OIDC_ISSUER", "")
    request = _FakeRequest({"authorization": "Bearer some-token"})
    assert await resolve_identity(request) is None


async def test_resolve_identity_none_without_bearer_header(enabled_auth):
    assert await resolve_identity(_FakeRequest({})) is None
    assert await resolve_identity(_FakeRequest({"authorization": "Basic dXNlcjpwdw=="})) is None


async def test_resolve_identity_skips_jwks_entry_missing_kid(enabled_auth, monkeypatch, rsa_key):
    jwks = {"keys": [{"kty": "RSA", "use": "sig"}, _jwk_for(rsa_key.public_key(), "good-kid")]}
    _patch_jwks(monkeypatch, _FakeResponse(jwks))
    token = _make_token(rsa_key, kid="good-kid")
    ident = await resolve_identity(_FakeRequest({"authorization": f"Bearer {token}"}))
    assert isinstance(ident, UserIdentity)
    assert ident.username == "alice"


async def test_resolve_identity_unknown_kid_raises(enabled_auth, monkeypatch, rsa_key):
    jwks = {"keys": [_jwk_for(rsa_key.public_key(), "good-kid")]}
    _patch_jwks(monkeypatch, _FakeResponse(jwks))
    token = _make_token(rsa_key, kid="other-kid")
    with pytest.raises(InvalidTokenError):
        await resolve_identity(_FakeRequest({"authorization": f"Bearer {token}"}))


async def test_resolve_identity_malformed_jwks_body_raises_invalid_token(
    enabled_auth, monkeypatch, rsa_key
):
    _patch_jwks(monkeypatch, _FakeResponse(invalid_json=True))
    token = _make_token(rsa_key, kid="any-kid")
    with pytest.raises(InvalidTokenError):
        await resolve_identity(_FakeRequest({"authorization": f"Bearer {token}"}))


async def test_resolve_identity_unknown_kid_does_not_stampede_jwks(enabled_auth, monkeypatch, rsa_key):
    """Two back-to-back requests with an unknown kid should fetch JWKS once.

    Without a negative-cache guard, every request bearing a bogus/unknown kid
    would force its own fresh JWKS fetch against the IdP — an unauthenticated
    caller could amplify load arbitrarily just by sending garbage kids.
    """
    jwks = {"keys": [_jwk_for(rsa_key.public_key(), "good-kid")]}
    fetch_count = 0

    class _CountingFakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            nonlocal fetch_count
            fetch_count += 1
            return _FakeResponse(jwks)

    monkeypatch.setattr(auth.httpx, "AsyncClient", _CountingFakeAsyncClient)

    token = _make_token(rsa_key, kid="other-kid")

    with pytest.raises(InvalidTokenError):
        await resolve_identity(_FakeRequest({"authorization": f"Bearer {token}"}))
    with pytest.raises(InvalidTokenError):
        await resolve_identity(_FakeRequest({"authorization": f"Bearer {token}"}))

    assert fetch_count == 1

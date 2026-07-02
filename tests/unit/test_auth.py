"""Unit tests for JWT decoding — static RSA keys, no network."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from teamrag.auth import InvalidTokenError, UserIdentity, decode_token

ISSUER = "http://localhost:8081/realms/teamrag"
AUDIENCE = "teamrag-gateway"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(key, *, groups=("squad-payments",), exp_delta=300, issuer=ISSUER, aud=AUDIENCE):
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
    return jwt.encode(payload, key, algorithm="RS256")


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

"""Bearer-token identity resolution for the gateway (Phase 7 squad ACLs).

``resolve_identity`` is the single entry point: FastAPI handlers call it with
the incoming request; ``None`` means "unauthenticated caller" (tier-0-only,
Phase 5 posture) and ``InvalidTokenError`` must surface as HTTP 401.
Signature keys come from the IdP's JWKS endpoint (cached in-process).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

_JWKS_CACHE: dict[str, Any] = {}   # jwks_url -> {"fetched_at": float, "keys": {kid: key}}
_JWKS_TTL_SECONDS = 300.0


class InvalidTokenError(Exception):
    """Presented Bearer token is malformed, expired, or fails verification."""


@dataclass(frozen=True)
class UserIdentity:
    sub: str
    username: str
    groups: tuple[str, ...]


def _normalize_groups(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(g).lstrip("/") for g in raw if str(g).lstrip("/"))


def decode_token(token: str, *, signing_key, issuer: str, audience: str | None) -> UserIdentity:
    """Verify signature/exp/iss (and aud when configured); return identity."""
    options = {"verify_aud": bool(audience)}
    try:
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience if audience else None,
            options=options,
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    return UserIdentity(
        sub=str(claims.get("sub", "")),
        username=str(claims.get("preferred_username", claims.get("sub", ""))),
        groups=_normalize_groups(claims.get("groups")),
    )


def _clear_jwks_cache() -> None:
    _JWKS_CACHE.clear()


async def _get_signing_key(jwks_url: str, kid: str):
    entry = _JWKS_CACHE.get(jwks_url)
    if entry is None or time.monotonic() - entry["fetched_at"] > _JWKS_TTL_SECONDS or kid not in entry["keys"]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            data = response.json()
        keys = {}
        for jwk_dict in data.get("keys", []):
            if jwk_dict.get("use") not in (None, "sig"):
                continue
            try:
                keys[jwk_dict["kid"]] = jwt.PyJWK(jwk_dict).key
            except jwt.PyJWKError:
                continue
        entry = {"fetched_at": time.monotonic(), "keys": keys}
        _JWKS_CACHE[jwks_url] = entry
    key = entry["keys"].get(kid)
    if key is None:
        raise InvalidTokenError(f"No signing key for kid={kid!r}")
    return key


async def resolve_identity(request: Any) -> UserIdentity | None:
    """Resolve caller identity from ``Authorization: Bearer`` or return None."""
    from teamrag.config import settings

    if not settings.OIDC_ISSUER:
        return None
    header = request.headers.get("authorization", "") if request is not None else ""
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if not token:
        return None

    try:
        unverified = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    jwks_url = f"{settings.OIDC_ISSUER.rstrip('/')}/protocol/openid-connect/certs"
    try:
        signing_key = await _get_signing_key(jwks_url, unverified.get("kid", ""))
    except httpx.HTTPError as exc:
        # Fail closed: cannot verify → treat as invalid rather than downgrade.
        raise InvalidTokenError(f"JWKS fetch failed: {exc}") from exc

    return decode_token(
        token,
        signing_key=signing_key,
        issuer=settings.OIDC_ISSUER,
        audience=settings.OIDC_AUDIENCE or None,
    )

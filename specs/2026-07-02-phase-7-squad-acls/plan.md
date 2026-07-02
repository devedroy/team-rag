# Phase 7 — Squad-level ACLs (Tier-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authenticated callers (Keycloak JWT) see `tier-0 ∪ their groups`; unauthenticated callers stay tier-0-only; private resources get squad tags at ingest from a Postgres mapping table refreshed by a sync job.

**Architecture:** Keycloak (docker-compose, dev realm committed to repo) issues JWTs carrying a `groups` claim. A new `teamrag.auth` module validates Bearer tokens against Keycloak's JWKS and yields a `UserIdentity`. `teamrag.acl` gains an `AUTHENTICATED_GROUPS` Qdrant filter mode. All retrieval endpoints (`/query`, `/document`, `/v1/chat/completions`) resolve identity, pass it to shared retrieval, and 401 on invalid tokens. Ingest connectors consult `resource_acl_mappings` (Postgres) to tag chunks; `python -m teamrag.sync` refreshes that table from Keycloak group attributes.

**Tech Stack:** FastAPI, PyJWT (`pyjwt[crypto]`), Keycloak 26 (`quay.io/keycloak/keycloak`), Qdrant filter DSL, SQLAlchemy 2 + Alembic, pytest.

## Global Constraints

- All I/O async (`httpx.AsyncClient`, `AsyncQdrantClient`, async SQLAlchemy) — no blocking calls in handlers.
- All API contracts via Pydantic models; config via `teamrag.config.Settings` + `.env.example` updated alongside.
- No token → **200 with tier-0-only results** (Phase 5 contract unchanged). Invalid/expired token → **401**.
- Group tag string == Keycloak group name (no leading `/`), e.g. `squad-payments`. Squad-tagged chunks also carry `tier-1`.
- Keycloak binds host port **8081** (8080 is TEI).
- New env vars: `OIDC_ISSUER`, `OIDC_AUDIENCE`, `KEYCLOAK_BASE_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD`, `TEAMRAG_BEARER_TOKEN`. Empty `OIDC_ISSUER` disables auth entirely (all callers unauthenticated) so existing deployments keep working.
- Never use starlette `TestClient`; use `AsyncClient` + `ASGITransport` (project test rule).
- Work on branch `phase-7-squad-acls` off `main`.

---

### Task 1: Keycloak service, realm import, config settings

**Files:**
- Create: `keycloak/realm-teamrag.json`
- Modify: `docker-compose.yml` (add `keycloak` service)
- Modify: `src/teamrag/config.py` (new settings)
- Modify: `.env.example` (new vars)

**Interfaces:**
- Produces: settings `settings.OIDC_ISSUER`, `settings.OIDC_AUDIENCE`, `settings.KEYCLOAK_BASE_URL`, `settings.KEYCLOAK_REALM`, `settings.KEYCLOAK_ADMIN_USER`, `settings.KEYCLOAK_ADMIN_PASSWORD`, `settings.TEAMRAG_BEARER_TOKEN` (all `str`). Running Keycloak at `http://localhost:8081`, realm `teamrag`, public client `teamrag-gateway` with direct-access grants, groups `squad-payments` & `squad-platform`, users `alice` (squad-payments, password `alice-password`) and `bob` (no groups, password `bob-password`). Access tokens contain `groups` (names, no slash) and audience `teamrag-gateway`.

- [ ] **Step 1: Add settings**

Append to `Settings` in `src/teamrag/config.py`:

```python
    # OIDC / Keycloak (Phase 7 squad ACLs); empty OIDC_ISSUER disables auth
    OIDC_ISSUER: str = ""                 # e.g. "http://localhost:8081/realms/teamrag"
    OIDC_AUDIENCE: str = "teamrag-gateway"
    KEYCLOAK_BASE_URL: str = "http://localhost:8081"
    KEYCLOAK_REALM: str = "teamrag"
    KEYCLOAK_ADMIN_USER: str = "admin"
    KEYCLOAK_ADMIN_PASSWORD: str = ""

    # MCP → gateway bearer token (optional; forwarded as Authorization header)
    TEAMRAG_BEARER_TOKEN: str = ""
```

- [ ] **Step 2: Add the same vars to `.env.example`** (with `OIDC_ISSUER=http://localhost:8081/realms/teamrag` and `KEYCLOAK_ADMIN_PASSWORD=admin` as dev defaults, commented as dev-only).

- [ ] **Step 3: Write realm import `keycloak/realm-teamrag.json`**

```json
{
  "realm": "teamrag",
  "enabled": true,
  "groups": [
    {"name": "squad-payments", "path": "/squad-payments"},
    {"name": "squad-platform", "path": "/squad-platform"}
  ],
  "users": [
    {
      "username": "alice",
      "enabled": true,
      "email": "alice@example.com",
      "emailVerified": true,
      "credentials": [{"type": "password", "value": "alice-password", "temporary": false}],
      "groups": ["/squad-payments"]
    },
    {
      "username": "bob",
      "enabled": true,
      "email": "bob@example.com",
      "emailVerified": true,
      "credentials": [{"type": "password", "value": "bob-password", "temporary": false}],
      "groups": []
    }
  ],
  "clients": [
    {
      "clientId": "teamrag-gateway",
      "enabled": true,
      "publicClient": true,
      "directAccessGrantsEnabled": true,
      "standardFlowEnabled": false,
      "protocol": "openid-connect",
      "protocolMappers": [
        {
          "name": "groups",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-group-membership-mapper",
          "consentRequired": false,
          "config": {
            "full.path": "false",
            "claim.name": "groups",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true"
          }
        },
        {
          "name": "audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "consentRequired": false,
          "config": {
            "included.client.audience": "teamrag-gateway",
            "access.token.claim": "true"
          }
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Add `keycloak` service to `docker-compose.yml`**

```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:26.1
    command: ["start-dev", "--import-realm", "--health-enabled=true"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KEYCLOAK_ADMIN_USER:-admin}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:-admin}
    ports:
      - "8081:8080"
    volumes:
      - ./keycloak:/opt/keycloak/data/import:ro
      - keycloak_data:/opt/keycloak/data/h2
    healthcheck:
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/9000' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 60s
    restart: unless-stopped
```

Add `keycloak_data:` under `volumes:`. (Keycloak image ships no curl/wget; bash TCP probe on the 9000 management port — same pattern as the qdrant fix.)

- [ ] **Step 5: Verify boot + realm import**

Run: `docker compose up -d keycloak` then poll until healthy, then:
`curl -s http://localhost:8081/realms/teamrag/.well-known/openid-configuration | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['issuer'])"`
Expected: `http://localhost:8081/realms/teamrag`

Then verify a password grant returns a token with groups:
```bash
curl -s -X POST http://localhost:8081/realms/teamrag/protocol/openid-connect/token \
  -d grant_type=password -d client_id=teamrag-gateway \
  -d username=alice -d password=alice-password | python3 -c "
import json,sys,base64
tok=json.load(sys.stdin)['access_token']
pad=lambda s:s+'='*(-len(s)%4)
print(json.loads(base64.urlsafe_b64decode(pad(tok.split('.')[1])))['groups'])"
```
Expected: `['squad-payments']`

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml keycloak/ src/teamrag/config.py .env.example
git commit -m "feat(acl): add Keycloak dev IdP with teamrag realm and OIDC settings"
```

---

### Task 2: `teamrag.auth` — JWT validation via JWKS

**Files:**
- Create: `src/teamrag/auth.py`
- Modify: `pyproject.toml` (add `pyjwt[crypto]>=2.8`)
- Test: `tests/unit/test_auth.py`

**Interfaces:**
- Produces:
  - `class UserIdentity` (frozen dataclass): `sub: str`, `username: str`, `groups: tuple[str, ...]`
  - `class InvalidTokenError(Exception)`
  - `async def resolve_identity(request) -> UserIdentity | None` — `None` when auth disabled (`settings.OIDC_ISSUER` empty) or no/`non-Bearer` `Authorization` header; raises `InvalidTokenError` for malformed/expired/bad-signature/wrong-issuer tokens.
  - `def decode_token(token: str, *, signing_key, issuer: str, audience: str | None) -> UserIdentity` — pure, unit-testable core.
  - `def _clear_jwks_cache() -> None` — test helper.

- [ ] **Step 1: Add dependency**

Add `"pyjwt[crypto]>=2.8"` to `[project] dependencies` in `pyproject.toml`; run `uv sync`.

- [ ] **Step 2: Write failing unit tests** `tests/unit/test_auth.py`

```python
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
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest tests/unit/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: teamrag.auth` (or ImportError).

- [ ] **Step 4: Implement `src/teamrag/auth.py`**

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/unit/test_auth.py -q`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/teamrag/auth.py tests/unit/test_auth.py
git commit -m "feat(acl): add JWT identity resolution against Keycloak JWKS"
```

---

### Task 3: `AUTHENTICATED_GROUPS` filter mode in `teamrag.acl`

**Files:**
- Modify: `src/teamrag/acl.py`
- Test: `tests/unit/test_acl.py` (append)

**Interfaces:**
- Consumes: `UserIdentity` (Task 2).
- Produces:
  - `AclFilterMode.AUTHENTICATED_GROUPS = "authenticated_groups"`
  - `qdrant_filter_for_mode(mode, user_groups: Sequence[str] = ())` — extended signature (default keeps all existing call sites valid).
  - `qdrant_filter_scroll_by_source_url(source_url_variants, mode, user_groups=())` — same extension.
  - `def resolve_acl_context(identity) -> tuple[AclFilterMode, tuple[str, ...]]` — `(UNAUTHENTICATED_TIER_0, ())` for `None`/no groups, else `(AUTHENTICATED_GROUPS, identity.groups)`.
  - `TIER_1_TAG: str = "tier-1"`

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_acl.py`:

```python
def test_qdrant_filter_authenticated_groups_includes_tier0_and_groups():
    from qdrant_client.models import FieldCondition, IsEmptyCondition, MatchAny, MatchValue

    from teamrag.acl import AclFilterMode, qdrant_filter_for_mode

    flt = qdrant_filter_for_mode(
        AclFilterMode.AUTHENTICATED_GROUPS, user_groups=("squad-payments", "squad-platform")
    )
    kinds = {type(c) for c in flt.should}
    assert IsEmptyCondition in kinds
    match_any = [c for c in flt.should if isinstance(c, FieldCondition) and isinstance(c.match, MatchAny)]
    assert match_any and set(match_any[0].match.any) == {"squad-payments", "squad-platform"}
    tier0 = [c for c in flt.should if isinstance(c, FieldCondition) and isinstance(c.match, MatchValue)]
    assert tier0 and tier0[0].match.value == "tier-0"


def test_qdrant_filter_authenticated_groups_without_groups_falls_back_to_tier0():
    from teamrag.acl import AclFilterMode, qdrant_filter_for_mode

    flt = qdrant_filter_for_mode(AclFilterMode.AUTHENTICATED_GROUPS, user_groups=())
    tier0_only = qdrant_filter_for_mode(AclFilterMode.UNAUTHENTICATED_TIER_0)
    assert flt == tier0_only


def test_resolve_acl_context():
    from teamrag.acl import AclFilterMode, resolve_acl_context
    from teamrag.auth import UserIdentity

    assert resolve_acl_context(None) == (AclFilterMode.UNAUTHENTICATED_TIER_0, ())
    ident = UserIdentity(sub="s", username="alice", groups=("squad-payments",))
    assert resolve_acl_context(ident) == (AclFilterMode.AUTHENTICATED_GROUPS, ("squad-payments",))
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_acl.py -q` — Expected: new tests FAIL (`AttributeError: AUTHENTICATED_GROUPS`).

- [ ] **Step 3: Implement in `src/teamrag/acl.py`**

Add `TIER_1_TAG: str = "tier-1"` next to `TIER_0_TAG`. Extend the enum:

```python
class AclFilterMode(str, enum.Enum):
    """How retrieval constrains Qdrant results."""

    UNAUTHENTICATED_TIER_0 = "unauthenticated_tier0"
    AUTHENTICATED_GROUPS = "authenticated_groups"
```

Replace `qdrant_filter_for_mode` with:

```python
def qdrant_filter_for_mode(mode: AclFilterMode, user_groups: "Sequence[str]" = ()):
    """Build a Qdrant ``Filter`` for the given ACL mode (lazy qdrant imports).

    Missing ``acl_tags`` is treated as tier-0 for backward compatibility.
    ``AUTHENTICATED_GROUPS`` widens visibility to tier-0 plus the caller's
    group tags; with no groups it degrades to the tier-0 filter.
    """
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        IsEmptyCondition,
        MatchAny,
        MatchValue,
        PayloadField,
    )

    should = [
        FieldCondition(key="acl_tags", match=MatchValue(value=TIER_0_TAG)),
        IsEmptyCondition(is_empty=PayloadField(key="acl_tags")),
    ]
    if mode is AclFilterMode.UNAUTHENTICATED_TIER_0:
        return Filter(should=should)
    if mode is AclFilterMode.AUTHENTICATED_GROUPS:
        groups = [str(g) for g in user_groups if str(g)]
        if groups:
            should.append(FieldCondition(key="acl_tags", match=MatchAny(any=groups)))
        return Filter(should=should)
    raise ValueError(f"Unsupported ACL filter mode: {mode!r}")
```

(Import `Sequence` from `collections.abc` under `TYPE_CHECKING` or plainly.) Extend the scroll helper the same way:

```python
def qdrant_filter_scroll_by_source_url(
    source_url_variants: list[str],
    mode: AclFilterMode,
    user_groups: "Sequence[str]" = (),
):
    """Qdrant scroll filter: ``source_url`` matches one of *variants* and ACL *mode* applies."""
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    tier = qdrant_filter_for_mode(mode, user_groups)
    return Filter(
        must=[
            FieldCondition(key="source_url", match=MatchAny(any=source_url_variants)),
            tier,
        ],
    )
```

Add the context resolver (import `UserIdentity` lazily to avoid cycles):

```python
def resolve_acl_context(identity) -> "tuple[AclFilterMode, tuple[str, ...]]":
    """Map an optional ``UserIdentity`` to (filter mode, group tags)."""
    if identity is None or not getattr(identity, "groups", ()):
        return (AclFilterMode.UNAUTHENTICATED_TIER_0, ())
    return (AclFilterMode.AUTHENTICATED_GROUPS, tuple(identity.groups))
```

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_acl.py -q` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/teamrag/acl.py tests/unit/test_acl.py
git commit -m "feat(acl): add AUTHENTICATED_GROUPS Qdrant filter mode"
```

---

### Task 4: Wire identity through `/query` and `/document` (401 on bad token)

**Files:**
- Modify: `src/teamrag/retrieval.py` (`semantic_search` accepts identity)
- Modify: `src/teamrag/api/query.py`
- Modify: `src/teamrag/api/document.py`
- Test: `tests/unit/test_api_auth_wiring.py` (new)

**Interfaces:**
- Consumes: `resolve_identity`, `InvalidTokenError` (Task 2); `resolve_acl_context` (Task 3).
- Produces: `semantic_search(..., identity: UserIdentity | None = None)` — when `identity` given, filter uses `resolve_acl_context(identity)`; else existing behavior. Endpoints raise `HTTPException(status_code=401, detail="Invalid or expired token")` on `InvalidTokenError`.

- [ ] **Step 1: Write failing tests** `tests/unit/test_api_auth_wiring.py`

```python
"""Auth wiring: bad Bearer tokens must 401 on /query and /document.

Uses ASGI transport with OIDC enabled via env; no live Keycloak needed —
a syntactically invalid token fails before any JWKS fetch.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from teamrag.config import settings
from teamrag.main import app


@pytest.fixture()
def oidc_enabled(monkeypatch):
    monkeypatch.setattr(settings, "OIDC_ISSUER", "http://localhost:8081/realms/teamrag")


@pytest.mark.asyncio
async def test_query_with_garbage_token_returns_401(oidc_enabled):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/query",
            json={"query": "x", "top_k": 1},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_document_with_garbage_token_returns_401(oidc_enabled):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/document",
            json={"source_url": "https://example.com/x"},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_query_without_token_still_200_when_oidc_enabled(oidc_enabled):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post("/query", json={"query": "x", "top_k": 1})
    assert response.status_code == 200
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_api_auth_wiring.py -q` — Expected: 401 tests FAIL (currently 200).

- [ ] **Step 3: Extend `semantic_search`** in `src/teamrag/retrieval.py`

Add parameter `identity: "UserIdentity | None" = None` (TYPE_CHECKING import from `teamrag.auth`). Replace the mode-resolution block at the top of `semantic_search`:

```python
    if identity is not None:
        acl_mode, user_groups = resolve_acl_context(identity)
    else:
        user_groups: tuple[str, ...] = ()
        if acl_mode is None:
            acl_mode = (
                resolve_acl_filter_mode_from_request(request)
                if request is not None
                else AclFilterMode.UNAUTHENTICATED_TIER_0
            )
    log_acl_filter_mode(acl_mode)
    q_filter = qdrant_filter_for_mode(acl_mode, user_groups)
```

Import `resolve_acl_context` from `teamrag.acl`.

- [ ] **Step 4: Wire `/query`** — in `src/teamrag/api/query.py` add imports `from fastapi import HTTPException` and `from teamrag.auth import InvalidTokenError, resolve_identity`; at the top of the handler (before the try/except that returns empty on failure):

```python
    try:
        identity = await resolve_identity(http_request)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
```

and pass `identity=identity` to `semantic_search`. Keep the existing broad `except Exception` around retrieval only (401 must not be swallowed).

- [ ] **Step 5: Wire `/document`** — in `src/teamrag/api/document.py`, same `resolve_identity` block; then:

```python
    acl_mode, user_groups = resolve_acl_context(identity)
    log_acl_filter_mode(acl_mode)
    flt = qdrant_filter_scroll_by_source_url(variants, acl_mode, user_groups)
```

replacing the current `resolve_acl_filter_mode_from_request` call. Import `resolve_acl_context` from `teamrag.acl`.

- [ ] **Step 6: Run** `uv run pytest tests/unit/test_api_auth_wiring.py tests/unit/test_acl.py -q` — Expected: pass. Also run full unit suite: `uv run pytest tests/unit -q`.

- [ ] **Step 7: Commit**

```bash
git add src/teamrag/retrieval.py src/teamrag/api/query.py src/teamrag/api/document.py tests/unit/test_api_auth_wiring.py
git commit -m "feat(acl): resolve caller identity on /query and /document; 401 on bad tokens"
```

---

### Task 5: Fix `/v1/chat/completions` ACL bypass

The chat endpoint currently retrieves via `teamrag.services.retrieval.retrieve_chunks`, which applies **no ACL filter** — a latent Phase 5 bug (invisible while everything is tier-0; a leak the moment tier-1 content exists).

**Files:**
- Modify: `src/teamrag/api/chat.py`
- Test: `tests/unit/test_api_auth_wiring.py` (append)

**Interfaces:**
- Consumes: `semantic_search(..., identity=...)` (Task 4), `resolve_identity` (Task 2).
- Produces: chat retrieval goes through the ACL-filtered `semantic_search`; bad token → 401 before any LLM call.

- [ ] **Step 1: Write failing test** — append to `tests/unit/test_api_auth_wiring.py`:

```python
@pytest.mark.asyncio
async def test_chat_with_garbage_token_returns_401(oidc_enabled):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 401
```

- [ ] **Step 2: Run** — Expected: FAIL (not 401).

- [ ] **Step 3: Implement** — in `src/teamrag/api/chat.py` handler `chat_completions`:
  - Add imports: `from teamrag.auth import InvalidTokenError, resolve_identity` and `from teamrag.retrieval import semantic_search`.
  - At the top of the handler:

```python
    try:
        identity = await resolve_identity(http_request)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
```

  - Replace the `retrieve_chunks(...)` call with:

```python
        hits = await semantic_search(
            query=user_query,
            top_k=settings.RAG_TOP_K,
            tei_url=settings.TEI_URL,
            qdrant_client=qdrant_client,
            collection_name=settings.QDRANT_COLLECTION,
            identity=identity,
        )
        chunks = [
            ChunkResult(
                content=h.content,
                source_url=h.source_url,
                page_title=h.page_title,
                score=float(h.score) if h.score is not None else 0.0,
            )
            for h in hits
        ]
```

  (Match the surrounding variable names — the existing code assigns the retrieval result to a `chunks` list of `ChunkResult`; keep its error handling that degrades to `chunks = []` on retrieval failure, but never swallow the 401.)

- [ ] **Step 4: Run** `uv run pytest tests/unit -q` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/teamrag/api/chat.py tests/unit/test_api_auth_wiring.py
git commit -m "fix(acl): route chat retrieval through ACL-filtered semantic_search"
```

---

### Task 6: Audit log rows on `/query`

**Files:**
- Modify: `src/teamrag/api/query.py`
- Test: `tests/integration/test_phase7_squad_acls.py` will assert rows (Task 10); unit smoke here.

**Interfaces:**
- Consumes: `AuditLog` model (`src/teamrag/db/models.py`), `get_session` (`src/teamrag/db/session.py`), `resolve_acl_context` (Task 3).
- Produces: one `audit_log` row per `/query` call: `caller_id` = identity `sub` or `"anonymous"`, `query_text`, `acl_tags_applied` = `["tier-0", *groups]`, `result_count`. Best-effort: audit failure logs a warning, never fails the query.

- [ ] **Step 1: Implement** — in `src/teamrag/api/query.py` after `chunks` is built:

```python
    caller_id = identity.sub if identity is not None else "anonymous"
    applied = ["tier-0", *(identity.groups if identity is not None else ())]
    try:
        async for session in get_session():
            session.add(
                AuditLog(
                    caller_id=caller_id,
                    query_text=request.query,
                    acl_tags_applied=applied,
                    result_count=len(chunks),
                )
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)
```

Imports: `from teamrag.db.models import AuditLog` and `from teamrag.db.session import get_session`.

- [ ] **Step 2: Verify** — run existing integration suite against live stack:
`uv run pytest tests/integration/test_phase0.py tests/integration/test_phase5_tier0_acls.py -q` — Expected: pass (audit write is additive).

- [ ] **Step 3: Commit**

```bash
git add src/teamrag/api/query.py
git commit -m "feat(acl): write audit_log rows for /query with caller and applied tags"
```

---

### Task 7: `resource_acl_mappings` table + ingest mapping helper

**Files:**
- Modify: `src/teamrag/db/models.py` (new model)
- Create: `alembic/versions/<generated>_add_resource_acl_mappings.py` (autogenerate)
- Create: `src/teamrag/ingest/acl_mapping.py`
- Test: `tests/unit/test_acl_mapping.py`

**Interfaces:**
- Produces:
  - Model `ResourceAclMapping`: `id: UUID pk`, `source_type: str`, `resource_key: str`, `acl_tags: list[str]` (`postgresql.ARRAY(Text)`, not null), `updated_at: datetime` (tz, server_default now, onupdate now); unique `(source_type, resource_key)` as `uq_resource_acl_mappings_type_key`.
  - `async def load_acl_mappings(session) -> dict[tuple[str, str], list[str]]`
  - `def resource_key_for_chunk(source_type: str, chunk: dict) -> str | None` — `github`→`chunk["repo"]`, `confluence`→`chunk["space_key"]`, `teams`→`chunk["channel_id"]`, `webex`→`chunk["space_id"]`; `None` when absent.
  - `def apply_acl_mapping(chunk: dict, source_type: str, mappings: dict[tuple[str, str], list[str]]) -> None` — sets `chunk["acl_tags"] = list(tags)` when a mapping matches; otherwise leaves the chunk untouched (tier-0 default downstream).

- [ ] **Step 1: Write failing tests** `tests/unit/test_acl_mapping.py`

```python
"""Unit tests for ingest-side resource → ACL tag mapping."""

from __future__ import annotations

from teamrag.ingest.acl_mapping import apply_acl_mapping, resource_key_for_chunk

MAPPINGS = {
    ("github", "org/payments-svc"): ["squad-payments", "tier-1"],
    ("teams", "channel-19:abc"): ["squad-payments", "tier-1"],
}


def test_resource_key_for_chunk_by_source_type():
    assert resource_key_for_chunk("github", {"repo": "org/payments-svc"}) == "org/payments-svc"
    assert resource_key_for_chunk("confluence", {"space_key": "ENG"}) == "ENG"
    assert resource_key_for_chunk("teams", {"channel_id": "channel-19:abc"}) == "channel-19:abc"
    assert resource_key_for_chunk("webex", {"space_id": "room-1"}) == "room-1"
    assert resource_key_for_chunk("github", {}) is None


def test_apply_acl_mapping_sets_tags_on_match():
    chunk = {"repo": "org/payments-svc", "acl_tags": ["tier-0"]}
    apply_acl_mapping(chunk, "github", MAPPINGS)
    assert chunk["acl_tags"] == ["squad-payments", "tier-1"]


def test_apply_acl_mapping_no_match_leaves_chunk_untouched():
    chunk = {"repo": "org/public-repo", "acl_tags": ["tier-0"]}
    apply_acl_mapping(chunk, "github", MAPPINGS)
    assert chunk["acl_tags"] == ["tier-0"]


def test_apply_acl_mapping_unknown_source_type_noop():
    chunk = {"acl_tags": ["tier-0"]}
    apply_acl_mapping(chunk, "jira", MAPPINGS)
    assert chunk["acl_tags"] == ["tier-0"]
```

- [ ] **Step 2: Run** — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Add model** to `src/teamrag/db/models.py`:

```python
class ResourceAclMapping(Base):
    __tablename__ = "resource_acl_mappings"
    __table_args__ = (
        sa.UniqueConstraint("source_type", "resource_key", name="uq_resource_acl_mappings_type_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_key: Mapped[str] = mapped_column(Text, nullable=False)
    acl_tags: Mapped[list[str]] = mapped_column(postgresql.ARRAY(Text), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
```

- [ ] **Step 4: Generate + apply migration**

```bash
export DATABASE_URL=postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag
uv run alembic revision --autogenerate -m "add resource_acl_mappings"
# review generated file: creates table + unique constraint, nothing else
uv run alembic upgrade head
```
Expected: `alembic current` shows the new revision at head.

- [ ] **Step 5: Implement `src/teamrag/ingest/acl_mapping.py`**

```python
"""Ingest-side lookup of resource → ACL tags (Phase 7 squad ACLs).

Connectors call ``apply_acl_mapping`` on each chunk before upsert; chunks with
no mapping keep their default tags (tier-0 via ``merge_acl_tags_for_ingest``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RESOURCE_KEY_FIELDS: dict[str, str] = {
    "github": "repo",
    "confluence": "space_key",
    "teams": "channel_id",
    "webex": "space_id",
}


async def load_acl_mappings(session) -> dict[tuple[str, str], list[str]]:
    """Load all resource→tags mappings from Postgres into a lookup dict."""
    from sqlalchemy import select

    from teamrag.db.models import ResourceAclMapping

    result = await session.execute(select(ResourceAclMapping))
    mappings: dict[tuple[str, str], list[str]] = {}
    for row in result.scalars():
        mappings[(row.source_type, row.resource_key)] = list(row.acl_tags)
    logger.info("Loaded %d resource ACL mappings", len(mappings))
    return mappings


def resource_key_for_chunk(source_type: str, chunk: dict) -> str | None:
    field = _RESOURCE_KEY_FIELDS.get(source_type)
    if field is None:
        return None
    value = chunk.get(field)
    return str(value) if value else None


def apply_acl_mapping(
    chunk: dict,
    source_type: str,
    mappings: dict[tuple[str, str], list[str]],
) -> None:
    key = resource_key_for_chunk(source_type, chunk)
    if key is None:
        return
    tags = mappings.get((source_type, key))
    if tags:
        chunk["acl_tags"] = list(tags)
```

- [ ] **Step 6: Run** `uv run pytest tests/unit/test_acl_mapping.py -q` — Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/teamrag/db/models.py alembic/versions/ src/teamrag/ingest/acl_mapping.py tests/unit/test_acl_mapping.py
git commit -m "feat(acl): resource_acl_mappings table and ingest mapping helper"
```

---

### Task 8: Wire mapping into the four ingest runners

**Files:**
- Modify: `src/teamrag/ingest/__main__.py` (all four `_run_*` coroutines)

**Interfaces:**
- Consumes: `load_acl_mappings`, `apply_acl_mapping` (Task 7).
- Produces: every chunk passes through `apply_acl_mapping(chunk, "<source_type>", mappings)` before `upsert_to_qdrant` / Postgres writes. Source types: `"confluence"`, `"github"`, `"teams"`, `"webex"`.

- [ ] **Step 1: Implement** — in each runner, load mappings once per run (inside the `async for session in get_session():` scope, before processing):

```python
            from teamrag.ingest.acl_mapping import apply_acl_mapping, load_acl_mappings

            mappings = await load_acl_mappings(session)
```

Then, immediately after chunks are produced and before embedding/upserting, e.g. Confluence:

```python
                chunks = chunk_document(page, settings.CONFLUENCE_URL)
                for chunk in chunks:
                    apply_acl_mapping(chunk, "confluence", mappings)
```

Repeat for GitHub (`"github"`, after `chunk_pr_document`), Teams (`"teams"`, after the thread chunk is built), and Webex (`"webex"`). Follow each runner's existing structure — the only change is the load + per-chunk apply.

- [ ] **Step 2: Verify imports and syntax**

Run: `uv run python -c "import teamrag.ingest.__main__ as m; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run full unit suite** `uv run pytest tests/unit -q` — Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/teamrag/ingest/__main__.py
git commit -m "feat(acl): apply resource ACL mappings during ingest for all connectors"
```

---

### Task 9: Sync job `python -m teamrag.sync`

**Files:**
- Create: `src/teamrag/sync/__init__.py`
- Create: `src/teamrag/sync/__main__.py`
- Test: `tests/unit/test_sync.py`

**Interfaces:**
- Consumes: settings `KEYCLOAK_BASE_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD`; model `ResourceAclMapping`.
- Produces:
  - `def mappings_from_groups(groups: list[dict]) -> list[tuple[str, str, list[str]]]` — pure: Keycloak group JSON → `(source_type, resource_key, tags)` rows. Group attributes `repos`→`github`, `spaces`→`confluence`, `channels`→`teams`, `rooms`→`webex`; tags are `[<group name>, "tier-1"]`.
  - `async def fetch_keycloak_groups(*, base_url, realm, admin_user, admin_password) -> list[dict]` — admin-cli password grant + `GET /admin/realms/{realm}/groups?briefRepresentation=false`.
  - `async def upsert_mappings(session, rows) -> int` — Postgres upsert on `uq_resource_acl_mappings_type_key`; returns row count.
  - CLI: `python -m teamrag.sync` (Keycloak pull) and `python -m teamrag.sync --seed github:org/payments-svc:squad-payments,tier-1` (repeatable, no Keycloak needed).

- [ ] **Step 1: Write failing tests** `tests/unit/test_sync.py`

```python
"""Unit tests for the Keycloak → resource_acl_mappings sync (pure parts)."""

from __future__ import annotations

from teamrag.sync import mappings_from_groups, parse_seed_arg


def test_mappings_from_groups_maps_attributes_to_source_types():
    groups = [
        {
            "name": "squad-payments",
            "attributes": {
                "repos": ["org/payments-svc", "org/billing"],
                "channels": ["19:abc"],
            },
        },
        {"name": "squad-platform", "attributes": {"rooms": ["room-1"], "spaces": ["PLAT"]}},
        {"name": "no-attrs", "attributes": {}},
    ]
    rows = mappings_from_groups(groups)
    assert ("github", "org/payments-svc", ["squad-payments", "tier-1"]) in rows
    assert ("github", "org/billing", ["squad-payments", "tier-1"]) in rows
    assert ("teams", "19:abc", ["squad-payments", "tier-1"]) in rows
    assert ("webex", "room-1", ["squad-platform", "tier-1"]) in rows
    assert ("confluence", "PLAT", ["squad-platform", "tier-1"]) in rows
    assert len(rows) == 5


def test_parse_seed_arg():
    assert parse_seed_arg("github:org/repo:squad-payments,tier-1") == (
        "github",
        "org/repo",
        ["squad-payments", "tier-1"],
    )


def test_parse_seed_arg_rejects_malformed():
    import pytest

    with pytest.raises(ValueError):
        parse_seed_arg("just-nonsense")
```

- [ ] **Step 2: Run** — Expected: FAIL (`ModuleNotFoundError: teamrag.sync`).

- [ ] **Step 3: Implement `src/teamrag/sync/__init__.py`**

```python
"""Refresh resource_acl_mappings from Keycloak group attributes (Phase 7).

Nightly-job entry point: ``python -m teamrag.sync``. Each Keycloak group may
carry multi-valued attributes ``repos`` / ``spaces`` / ``channels`` / ``rooms``
naming the resources its members may see; each named resource is mapped to
``[<group-name>, "tier-1"]``.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_ATTRIBUTE_SOURCE_TYPES: dict[str, str] = {
    "repos": "github",
    "spaces": "confluence",
    "channels": "teams",
    "rooms": "webex",
}

MappingRow = tuple[str, str, list[str]]


def mappings_from_groups(groups: list[dict]) -> list[MappingRow]:
    rows: list[MappingRow] = []
    for group in groups:
        name = str(group.get("name", "")).strip()
        if not name:
            continue
        tags = [name, "tier-1"]
        attributes = group.get("attributes") or {}
        for attr, source_type in _ATTRIBUTE_SOURCE_TYPES.items():
            for resource_key in attributes.get(attr, []) or []:
                resource_key = str(resource_key).strip()
                if resource_key:
                    rows.append((source_type, resource_key, list(tags)))
    return rows


def parse_seed_arg(value: str) -> MappingRow:
    parts = value.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"--seed must look like source_type:resource_key:tag1,tag2 — got {value!r}"
        )
    source_type, resource_key, tags_csv = parts
    tags = [t.strip() for t in tags_csv.split(",") if t.strip()]
    if not tags:
        raise ValueError(f"--seed has no tags: {value!r}")
    return (source_type, resource_key, tags)


async def fetch_keycloak_groups(
    *, base_url: str, realm: str, admin_user: str, admin_password: str
) -> list[dict]:
    """Fetch all groups (with attributes) via the Keycloak admin REST API."""
    token_url = f"{base_url.rstrip('/')}/realms/master/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        groups_response = await client.get(
            f"{base_url.rstrip('/')}/admin/realms/{realm}/groups",
            params={"briefRepresentation": "false"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        groups_response.raise_for_status()
        return groups_response.json()


async def upsert_mappings(session, rows: list[MappingRow]) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.db.models import ResourceAclMapping

    count = 0
    for source_type, resource_key, tags in rows:
        stmt = (
            pg_insert(ResourceAclMapping)
            .values(source_type=source_type, resource_key=resource_key, acl_tags=tags)
            .on_conflict_do_update(
                constraint="uq_resource_acl_mappings_type_key",
                set_={"acl_tags": tags},
            )
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    return count
```

- [ ] **Step 4: Implement `src/teamrag/sync/__main__.py`**

```python
"""CLI: python -m teamrag.sync [--seed source_type:resource_key:tag1,tag2 ...]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _run(seed_rows) -> None:
    from teamrag.config import settings
    from teamrag.db.session import get_session
    from teamrag.sync import fetch_keycloak_groups, mappings_from_groups, upsert_mappings

    if seed_rows:
        rows = seed_rows
        logger.info("Seeding %d mapping(s) from CLI args", len(rows))
    else:
        if not settings.KEYCLOAK_ADMIN_PASSWORD:
            logger.error("KEYCLOAK_ADMIN_PASSWORD is not set; cannot sync from Keycloak.")
            sys.exit(1)
        groups = await fetch_keycloak_groups(
            base_url=settings.KEYCLOAK_BASE_URL,
            realm=settings.KEYCLOAK_REALM,
            admin_user=settings.KEYCLOAK_ADMIN_USER,
            admin_password=settings.KEYCLOAK_ADMIN_PASSWORD,
        )
        rows = mappings_from_groups(groups)
        logger.info("Fetched %d group(s) → %d mapping(s) from Keycloak", len(groups), len(rows))

    async for session in get_session():
        written = await upsert_mappings(session, rows)
        logger.info("Upserted %d resource ACL mapping(s)", written)


def main() -> None:
    from teamrag.sync import parse_seed_arg

    parser = argparse.ArgumentParser(description="Refresh resource_acl_mappings")
    parser.add_argument(
        "--seed",
        action="append",
        default=[],
        metavar="SOURCE_TYPE:RESOURCE_KEY:TAG1,TAG2",
        help="Upsert one mapping directly instead of syncing from Keycloak (repeatable)",
    )
    args = parser.parse_args()
    try:
        seed_rows = [parse_seed_arg(s) for s in args.seed]
    except ValueError as exc:
        parser.error(str(exc))
    asyncio.run(_run(seed_rows))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run** `uv run pytest tests/unit/test_sync.py -q` — Expected: pass.
Also smoke the CLI parser: `uv run python -m teamrag.sync --help` — Expected: usage text, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/teamrag/sync/ tests/unit/test_sync.py
git commit -m "feat(acl): add Keycloak group sync job for resource ACL mappings"
```

---

### Task 10: MCP bearer forwarding

**Files:**
- Modify: `src/teamrag/mcp_server/gateway_client.py`
- Test: `tests/unit/test_mcp_gateway_auth.py`

**Interfaces:**
- Consumes: `settings.TEAMRAG_BEARER_TOKEN` (Task 1).
- Produces: `TeamRagGateway` accepts `bearer_token: str | None = None` kwarg (defaults to `settings.TEAMRAG_BEARER_TOKEN` when falsy → no header). When set, every request carries `Authorization: Bearer <token>`.

- [ ] **Step 1: Write failing test** `tests/unit/test_mcp_gateway_auth.py`

```python
"""TeamRagGateway must forward its bearer token to the gateway."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request

from teamrag.mcp_server.gateway_client import TeamRagGateway

echo_app = FastAPI()


@echo_app.post("/query")
async def echo_query(request: Request):
    return {"auth": request.headers.get("authorization", ""), "chunks": [], "total": 0}


@pytest.mark.asyncio
async def test_gateway_sends_bearer_when_configured():
    gateway = TeamRagGateway(asgi_app=echo_app, bearer_token="tok-123")
    data = await gateway.post_query("q", 1)
    assert data["auth"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_gateway_omits_header_without_token():
    gateway = TeamRagGateway(asgi_app=echo_app, bearer_token=None)
    data = await gateway.post_query("q", 1)
    assert data["auth"] == ""
```

- [ ] **Step 2: Run** — Expected: FAIL (`TypeError: unexpected keyword argument 'bearer_token'`).

- [ ] **Step 3: Implement** — in `TeamRagGateway.__init__` add:

```python
        bearer_token: str | None = None,
```

and body:

```python
        if bearer_token is None:
            bearer_token = settings.TEAMRAG_BEARER_TOKEN or None
        self._bearer_token = bearer_token
```

Careful: the default must only kick in when the caller doesn't pass the kwarg; passing `bearer_token=None` explicitly also falls back to settings — acceptable, and the test env has `TEAMRAG_BEARER_TOKEN` empty so both paths agree. In `_post_json`, build headers:

```python
        headers = {"Authorization": f"Bearer {self._bearer_token}"} if self._bearer_token else {}
```

and pass `headers=headers` to both `client.post(...)` calls.

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_mcp_gateway_auth.py tests/unit/test_mcp_handlers.py -q` — Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/teamrag/mcp_server/gateway_client.py tests/unit/test_mcp_gateway_auth.py
git commit -m "feat(mcp): forward optional bearer token to the gateway"
```

---

### Task 11: End-to-end integration test + validation doc + docs

**Files:**
- Create: `tests/integration/test_phase7_squad_acls.py`
- Create: `specs/2026-07-02-phase-7-squad-acls/validation.md` (fill in after runs; `git add -f`)
- Modify: `README.md` (Phase 7 section: Keycloak, sync job, auth env vars)
- Modify: `CLAUDE.md` (ACL model section: Phase 7 shipped; also correct the stale "~768-dim" note to 1024)

**Interfaces:**
- Consumes: everything above; live docker-compose stack incl. Keycloak.

- [ ] **Step 1: Write the integration test**

```python
"""Phase 7 — squad ACL end-to-end: token from live Keycloak gates tier-1 chunks.

Roadmap "done when": a user in squad-payments sees private payments results;
a user outside does not. Skips gracefully when Keycloak/TEI are unreachable.
"""

from __future__ import annotations

import uuid as uuid_mod

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

from teamrag.config import settings
from teamrag.retrieval import embed_query_text

PROBE_QUERY = "phase7 squad payments secret rollout plan"
TIER1_CONTENT = "phase7 squad payments secret rollout plan: canary at 5 percent"
TIER1_SOURCE_URL = "https://example.com/private/payments-rollout"

KEYCLOAK_TOKEN_URL = (
    f"{settings.KEYCLOAK_BASE_URL}/realms/{settings.KEYCLOAK_REALM}"
    "/protocol/openid-connect/token"
)


async def _password_grant(username: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "teamrag-gateway",
                "username": username,
                "password": password,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


@pytest.fixture()
async def keycloak_or_skip():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.KEYCLOAK_BASE_URL}/realms/{settings.KEYCLOAK_REALM}"
                "/.well-known/openid-configuration"
            )
            response.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Keycloak not reachable: {exc}")


@pytest.fixture()
async def tier1_point(keycloak_or_skip, monkeypatch):
    monkeypatch.setattr(
        settings, "OIDC_ISSUER", f"{settings.KEYCLOAK_BASE_URL}/realms/{settings.KEYCLOAK_REALM}"
    )
    try:
        vector = await embed_query_text(TIER1_CONTENT, settings.TEI_URL)
    except Exception as exc:
        pytest.skip(f"TEI not reachable: {exc}")

    point_id = int(uuid_mod.uuid4().hex[:15], 16)
    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await client.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "content": TIER1_CONTENT,
                        "source_url": TIER1_SOURCE_URL,
                        "page_title": "Payments rollout (private)",
                        "acl_tags": ["squad-payments", "tier-1"],
                    },
                )
            ],
            wait=True,
        )
        yield point_id
    finally:
        try:
            await client.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=[point_id],
                wait=True,
            )
        finally:
            await client.close()


def _urls(chunks: list[dict]) -> set[str]:
    return {c["source_url"] for c in chunks}


@pytest.mark.asyncio
async def test_squad_member_sees_tier1_chunk(tier1_point):
    from teamrag.main import app

    token = await _password_grant("alice", "alice-password")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/query",
            json={"query": PROBE_QUERY, "top_k": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert TIER1_SOURCE_URL in _urls(response.json()["chunks"])


@pytest.mark.asyncio
async def test_anonymous_cannot_see_tier1_chunk(tier1_point):
    from teamrag.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post("/query", json={"query": PROBE_QUERY, "top_k": 10})
    assert response.status_code == 200
    assert TIER1_SOURCE_URL not in _urls(response.json()["chunks"])


@pytest.mark.asyncio
async def test_other_squad_user_cannot_see_tier1_chunk(tier1_point):
    from teamrag.main import app

    token = await _password_grant("bob", "bob-password")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/query",
            json={"query": PROBE_QUERY, "top_k": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert TIER1_SOURCE_URL not in _urls(response.json()["chunks"])


@pytest.mark.asyncio
async def test_invalid_token_is_401(keycloak_or_skip, monkeypatch):
    from teamrag.main import app

    monkeypatch.setattr(
        settings, "OIDC_ISSUER", f"{settings.KEYCLOAK_BASE_URL}/realms/{settings.KEYCLOAK_REALM}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/query",
            json={"query": PROBE_QUERY, "top_k": 5},
            headers={"Authorization": "Bearer garbage.token.here"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_audit_log_records_squad_query(tier1_point):
    from sqlalchemy import select

    from teamrag.db.models import AuditLog
    from teamrag.db.session import get_session
    from teamrag.main import app

    token = await _password_grant("alice", "alice-password")
    marker_query = f"audit-marker-{uuid_mod.uuid4().hex}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        await client.post(
            "/query",
            json={"query": marker_query, "top_k": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
    async for session in get_session():
        result = await session.execute(
            select(AuditLog).where(AuditLog.query_text == marker_query)
        )
        row = result.scalar_one()
        assert row.caller_id != "anonymous"
        assert "squad-payments" in (row.acl_tags_applied or [])
```

Note: `tests/conftest.py` must keep `DATABASE_URL` handling as-is; these tests rely on the live stack and skip when Keycloak/TEI are down.

- [ ] **Step 2: Run the whole suite against the live stack**

```bash
docker compose up -d && sleep 5
export DATABASE_URL=postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag
uv run pytest tests/ -v
```
Expected: all pass (LLM-dependent phase-3 tests may skip).

- [ ] **Step 3: Manual smoke of the sync job**

```bash
uv run python -m teamrag.sync --seed "github:org/payments-svc:squad-payments,tier-1"
```
Expected: log line `Upserted 1 resource ACL mapping(s)`; verify with
`docker compose exec postgres psql -U teamrag -c "select source_type, resource_key, acl_tags from resource_acl_mappings;"`

- [ ] **Step 4: Write `validation.md`** — record: commands run, suite results, the roadmap "done when" evidence (alice sees / bob & anonymous don't), sync-job output. Follow the structure of `specs/2026-05-13-phase-5-tier-0-acls/validation.md`.

- [ ] **Step 5: Update docs** — README (Phase 7 section + new env vars + `python -m teamrag.sync` usage + Keycloak dev logins), CLAUDE.md (ACL model section now includes Phase 7 behavior; fix "~768-dim" → "1024-dim").

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_phase7_squad_acls.py README.md CLAUDE.md
git add -f specs/2026-07-02-phase-7-squad-acls/validation.md
git commit -m "test(acl): Phase 7 end-to-end squad ACL tests + docs"
```

---

## Self-review notes

- Spec coverage: Keycloak compose+realm (T1), JWT validation (T2), filter mode (T3), endpoint wiring incl. 401 & unauthenticated=tier-0 (T4), chat ACL bypass fix (T5), audit (T6), mapping table + ingest lookup (T7–8), sync job (T9), MCP bearer (T10), acceptance test (T11). Spec's "MCP forwards optional token" ↔ T10; "no token → 200 tier-0" ↔ T4 test 3 + T11 anonymous test.
- Type consistency: `UserIdentity.groups: tuple[str, ...]` everywhere; `semantic_search(identity=...)` used in T4/T5/T11; `qdrant_filter_for_mode(mode, user_groups)` positional-compatible with existing call sites.

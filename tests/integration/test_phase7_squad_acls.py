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

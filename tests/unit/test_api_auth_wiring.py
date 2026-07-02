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

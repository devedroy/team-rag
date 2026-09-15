"""Audit logging on /query: a row is attempted on every path, including retrieval failure.

Uses ASGI transport (no live server) and monkeypatches semantic_search to raise,
plus a stub get_session that records added AuditLog rows — no live DB needed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import teamrag.api.query as query_module
from teamrag.db.models import AuditLog
from teamrag.main import app


class _StubSession:
    def __init__(self, added: list):
        self._added = added
        self.committed = False

    def add(self, obj):
        self._added.append(obj)

    async def commit(self):
        self.committed = True


@pytest.fixture()
def audit_rows(monkeypatch):
    """Replace get_session with a stub that records added objects."""
    added: list = []

    async def _stub_get_session():
        yield _StubSession(added)

    monkeypatch.setattr(query_module, "get_session", _stub_get_session)
    return added


@pytest.mark.asyncio
async def test_query_audits_even_when_retrieval_fails(monkeypatch, audit_rows):
    async def _boom(**kwargs):
        raise RuntimeError("TEI is down")

    monkeypatch.setattr(query_module, "semantic_search", _boom)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post("/query", json={"query": "marker-q", "top_k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["chunks"] == []
    assert body["total"] == 0

    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert isinstance(row, AuditLog)
    assert row.caller_id == "anonymous"
    assert row.query_text == "marker-q"
    assert row.acl_tags_applied == ["tier-0"]
    assert row.result_count == 0


@pytest.mark.asyncio
async def test_query_audits_on_success(monkeypatch, audit_rows):
    async def _empty(**kwargs):
        return []

    monkeypatch.setattr(query_module, "semantic_search", _empty)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post("/query", json={"query": "ok-q", "top_k": 3})

    assert response.status_code == 200
    assert len(audit_rows) == 1
    assert audit_rows[0].query_text == "ok-q"


@pytest.mark.asyncio
async def test_query_survives_audit_write_failure(monkeypatch):
    async def _empty(**kwargs):
        return []

    async def _broken_get_session():
        raise RuntimeError("DB is down")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(query_module, "semantic_search", _empty)
    monkeypatch.setattr(query_module, "get_session", _broken_get_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post("/query", json={"query": "x", "top_k": 1})

    assert response.status_code == 200
    assert response.json() == {"chunks": [], "total": 0}

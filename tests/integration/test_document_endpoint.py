"""Tests for POST /document (Qdrant scroll by source_url)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from teamrag.main import app

pytestmark = pytest.mark.asyncio


async def test_document_returns_chunks_sorted_by_chunk_index():
    mock_client = MagicMock()
    mock_client.scroll = AsyncMock(
        return_value=(
            [
                MagicMock(
                    payload={
                        "content": "second",
                        "source_url": "https://example.com/page",
                        "page_title": "P",
                        "chunk_index": 2,
                    }
                ),
                MagicMock(
                    payload={
                        "content": "first",
                        "source_url": "https://example.com/page",
                        "page_title": "P",
                        "chunk_index": 0,
                    }
                ),
            ],
            None,
        )
    )

    prev = getattr(app.state, "qdrant_client", None)
    try:
        app.state.qdrant_client = mock_client
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/document",
                json={"source_url": "https://example.com/page"},
            )
    finally:
        app.state.qdrant_client = prev

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [c["content"] for c in body["chunks"]] == ["first", "second"]
    assert all(c["score"] == 0.0 for c in body["chunks"])

    assert mock_client.scroll.called
    _args, scroll_kw = mock_client.scroll.call_args
    scroll_filter = scroll_kw.get("scroll_filter")
    assert scroll_filter is not None
    assert scroll_filter.must is not None
    assert len(scroll_filter.must) == 2


async def test_document_rejects_whitespace_only_url():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/document", json={"source_url": "   "})
    assert response.status_code == 422

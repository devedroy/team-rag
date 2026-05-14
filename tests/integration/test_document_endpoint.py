"""Tests for POST /document (Qdrant scroll by source_url)."""

from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from teamrag.main import app


def test_document_returns_chunks_sorted_by_chunk_index():
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
        with TestClient(app) as client:
            app.state.qdrant_client = mock_client
            response = client.post(
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


def test_document_rejects_whitespace_only_url():
    with TestClient(app) as client:
        response = client.post("/document", json={"source_url": "   "})
    assert response.status_code == 422

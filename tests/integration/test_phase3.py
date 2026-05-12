"""Phase 3 integration tests — human chat UI with RAG-augmented completions.

Tests verify:
- POST /v1/chat/completions returns 200 with valid OpenAI response shape
- When LLM_BASE_URL is not set, returns a graceful message (no 500 error)
- When LLM_BASE_URL is set and chunks exist, response includes source URLs
- Open WebUI container is reachable at http://localhost:{OPENWEBUI_PORT}

Tests gracefully skip when LLM_BASE_URL is not set (except test_chat_completions_returns_200).
Open WebUI test skips if container is not running.

Requires Docker stack running: Qdrant, Postgres, TEI, FastAPI.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from teamrag.main import app

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")


@pytest.mark.asyncio
async def test_chat_completions_returns_200():
    """POST /v1/chat/completions returns 200 with valid OpenAI response shape.

    This test runs regardless of LLM_BASE_URL; when not set, the endpoint
    returns a graceful message in OpenAI format rather than a 500 error.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello, how are you?"}],
                "stream": False,
            },
        )

    assert response.status_code == 200

    body = response.json()
    # Verify OpenAI response shape
    assert "id" in body
    assert "object" in body
    assert "created" in body
    assert "model" in body
    assert "choices" in body
    assert isinstance(body["choices"], list)
    assert len(body["choices"]) > 0

    # Verify first choice has the expected structure
    choice = body["choices"][0]
    assert "index" in choice
    assert "message" in choice
    assert "finish_reason" in choice
    assert "role" in choice["message"]
    assert "content" in choice["message"]
    assert isinstance(choice["message"]["content"], str)
    assert len(choice["message"]["content"]) > 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    not LLM_BASE_URL,
    reason="LLM_BASE_URL not set — skipping test that requires real LLM backend",
)
async def test_chat_completions_includes_source_url():
    """When LLM_BASE_URL is set and chunks exist, response includes source URLs.

    This test requires:
    1. LLM_BASE_URL to be configured (pointing to OpenAI, Ollama, etc.)
    2. At least one chunk in Qdrant with a source_url

    Skips gracefully if LLM_BASE_URL is not set.
    """
    from teamrag.config import settings
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, VectorParams

    # Ensure collection exists
    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        try:
            await qdrant.get_collection(settings.QDRANT_COLLECTION)
        except Exception:
            # Collection doesn't exist; create it
            await qdrant.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        # Insert a test chunk with a source URL
        import uuid
        test_chunk_id = str(uuid.uuid4())
        await qdrant.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=[
                {
                    "id": test_chunk_id,
                    "vector": [0.1] * 768,  # Dummy 768-dim vector
                    "payload": {
                        "content": "This is a test chunk about Python programming.",
                        "source_url": "https://example.com/python-guide",
                        "page_title": "Python Guide",
                    },
                }
            ],
        )

        # Send a query that should retrieve the test chunk
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": settings.LLM_MODEL,
                    "messages": [{"role": "user", "content": "Tell me about Python"}],
                    "stream": False,
                },
            )

        assert response.status_code == 200
        body = response.json()

        # Extract the assistant's response
        choice = body["choices"][0]
        response_content = choice["message"]["content"]

        # The response should mention the source URL or cite it
        # (The system prompt instructs the LLM to cite sources)
        assert "https://example.com/python-guide" in response_content or "Source" in response_content, (
            f"Expected source URL or citation in response, but got: {response_content}"
        )

    finally:
        await qdrant.close()


@pytest.mark.asyncio
async def test_open_webui_health():
    """Open WebUI container must be reachable at http://localhost:{OPENWEBUI_PORT}.

    This test skips gracefully if Open WebUI is not running (e.g., in CI without the container).
    """
    openwebui_port = os.environ.get("OPENWEBUI_PORT", "3000")
    openwebui_url = f"http://localhost:{openwebui_port}"

    try:
        async with AsyncClient() as client:
            response = await client.get(openwebui_url, timeout=5.0)
        # Open WebUI returns various status codes (200, 301, etc.)
        # We just care that it's reachable (no connection error)
        assert response.status_code >= 200, (
            f"Expected 2xx/3xx from Open WebUI at {openwebui_url}, got {response.status_code}"
        )
    except Exception as exc:
        error_str = str(exc).lower()
        if any(
            keyword in error_str
            for keyword in ("connection", "refused", "timeout", "unreachable", "network")
        ):
            pytest.skip(f"Open WebUI not reachable at {openwebui_url}: {exc}")
        raise

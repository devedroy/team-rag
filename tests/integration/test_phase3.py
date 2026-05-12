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

import logging
import os

import pytest
from httpx import ASGITransport, AsyncClient

from teamrag.main import app

logger = logging.getLogger(__name__)

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")


@pytest.mark.asyncio
@pytest.mark.skipif(
    bool(LLM_BASE_URL),
    reason="LLM_BASE_URL is set — this test covers the unconfigured path only",
)
async def test_chat_completions_returns_200():
    """POST /v1/chat/completions returns 200 with valid OpenAI response shape.

    Verifies the graceful degradation path when LLM_BASE_URL is not configured.
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
    from teamrag.services.retrieval import embed_query
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, VectorParams, PointIdsList
    import uuid

    # TEI must be reachable for retrieval to work; skip if not available
    try:
        await embed_query("preflight", settings.TEI_URL)
    except Exception as exc:
        pytest.skip(f"TEI embedding service not reachable — cannot exercise retrieval path: {exc}")

    # Create Qdrant client and ensure collection exists
    qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        try:
            await qdrant_client.get_collection(settings.QDRANT_COLLECTION)
        except Exception:
            # Collection doesn't exist; create it
            await qdrant_client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        # Insert a test chunk with a source URL
        test_chunk_id = str(uuid.uuid4())
        await qdrant_client.upsert(
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

        # Set the Qdrant client on app state for the ASGI request
        # (ASGITransport does not run the lifespan, so we must do this manually)
        app.state.qdrant_client = qdrant_client

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

        # The response should cite the source URL per the system prompt instructions.
        # Note: this assertion is LLM-dependent — if the model paraphrases without
        # quoting the URL exactly, this will fail. Run manually against a known-good LLM.
        assert "https://example.com/python-guide" in response_content, (
            f"Expected source URL in response, but got: {response_content}"
        )

    finally:
        # Clean up: delete test point and restore app state
        try:
            await qdrant_client.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=PointIdsList(points=[test_chunk_id]),
            )
        except Exception as exc:
            logger.warning("Failed to delete test chunk: %s", exc)

        app.state.qdrant_client = None
        await qdrant_client.close()


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
        # We just care that it's reachable (not a 5xx error)
        assert response.status_code < 500, (
            f"Open WebUI returned unexpected status {response.status_code}"
        )
    except Exception as exc:
        error_str = str(exc).lower()
        if any(
            keyword in error_str
            for keyword in ("connection", "refused", "timeout", "unreachable", "network")
        ):
            pytest.skip(f"Open WebUI not reachable at {openwebui_url}: {exc}")
        raise

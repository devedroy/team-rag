"""Phase 1 integration tests — require live Docker stack + CONFLUENCE_API_TOKEN.

These tests verify:
- Qdrant collection contains >0 points after ingestion
- POST /query returns ≥1 chunk with required metadata fields
- Chunk metadata contains all required fields (content, source_url, page_title, score)
- Ingest is idempotent (running twice doesn't duplicate points)

Tests gracefully skip when CONFLUENCE_API_TOKEN is not set.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams

pytestmark = pytest.mark.asyncio

CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")
skip_if_no_confluence = pytest.mark.skipif(
    not CONFLUENCE_TOKEN,
    reason="CONFLUENCE_API_TOKEN not set — skipping Confluence integration tests",
)


async def _ensure_collection(client: AsyncQdrantClient, name: str) -> None:
    """Create Qdrant collection if it doesn't exist."""
    try:
        await client.get_collection(name)
    except Exception:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


@skip_if_no_confluence
async def test_qdrant_point_count_positive():
    """After ingestion, Qdrant collection must contain at least one point."""
    from teamrag.config import settings

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await _ensure_collection(client, settings.QDRANT_COLLECTION)
        info = await client.get_collection(settings.QDRANT_COLLECTION)
        assert info.points_count > 0, (
            "Qdrant collection is empty — run 'uv run python -m teamrag.ingest confluence' first"
        )
    except Exception as exc:
        error_str = str(exc).lower()
        if any(
            keyword in error_str
            for keyword in ("connection", "refused", "timeout", "unreachable", "network")
        ):
            pytest.skip(f"Qdrant not reachable at {settings.QDRANT_URL}: {exc}")
        raise
    finally:
        await client.close()


@skip_if_no_confluence
async def test_query_returns_chunks_after_ingest():
    """POST /query must return ≥1 chunk with non-empty source_url after ingestion."""
    from teamrag.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/query", json={"query": "architecture overview", "top_k": 5}
        )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["chunks"], list)
    assert len(data["chunks"]) >= 1, "Expected ≥1 chunk — is ingestion complete?"
    assert data["total"] == len(data["chunks"])
    first = data["chunks"][0]
    assert first["source_url"], "source_url must be non-empty"
    assert first["content"], "content must be non-empty"


@skip_if_no_confluence
async def test_chunk_metadata_fields():
    """Every returned chunk must have all required metadata fields non-empty."""
    from teamrag.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/query", json={"query": "overview", "top_k": 5})

    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert chunks, "Expected chunks in response — is ingestion complete?"

    required_fields = {"content", "source_url", "page_title", "score"}
    for chunk in chunks:
        missing = required_fields - chunk.keys()
        assert not missing, f"Chunk missing fields: {missing}"
        assert chunk["content"], "content must not be empty"
        assert chunk["source_url"], "source_url must not be empty"


@skip_if_no_confluence
async def test_ingest_is_idempotent():
    """Running ingest twice must not increase the Qdrant point count."""
    from teamrag.config import settings
    from teamrag.ingest.confluence import ConfluenceClient
    from teamrag.ingest.pipeline import chunk_document, embed_chunks, upsert_to_qdrant

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await _ensure_collection(client, settings.QDRANT_COLLECTION)

        info_before = await client.get_collection(settings.QDRANT_COLLECTION)
        count_before = info_before.points_count or 0

        confluence = ConfluenceClient(settings)
        space_keys = [
            k.strip() for k in settings.CONFLUENCE_SPACE_KEYS.split(",") if k.strip()
        ]
        if not space_keys:
            pytest.skip("CONFLUENCE_SPACE_KEYS not set")

        pages_done = 0
        async for page in confluence.fetch_pages(space_keys[0]):
            chunks = chunk_document(page, settings.CONFLUENCE_URL)
            if not chunks:
                continue
            vectors = await embed_chunks(chunks, settings.TEI_URL)
            await upsert_to_qdrant(chunks, vectors, client, settings.QDRANT_COLLECTION)
            pages_done += 1
            if pages_done >= 1:
                break

        info_after = await client.get_collection(settings.QDRANT_COLLECTION)
        count_after = info_after.points_count or 0

        assert count_after == count_before, (
            f"Point count changed from {count_before} to {count_after} on second ingest run — "
            "upsert is not idempotent"
        )

    except Exception as exc:
        error_str = str(exc).lower()
        if any(
            keyword in error_str
            for keyword in ("connection", "refused", "timeout", "unreachable", "network")
        ):
            pytest.skip(f"Qdrant not reachable at {settings.QDRANT_URL}: {exc}")
        raise
    finally:
        await client.close()

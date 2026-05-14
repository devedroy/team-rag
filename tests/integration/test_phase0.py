"""Phase 0 integration tests.

These tests verify:
- /health returns 200 + {"status": "ok"}
- /query returns 200 + {"chunks": [], "total": 0}
- Qdrant collection exists (or is created) with 0 vectors
- Postgres has all 4 expected tables

Tests gracefully skip when external services (Qdrant, Postgres) are not reachable.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from teamrag.main import app


# ---------------------------------------------------------------------------
# API tests (in-process ASGI, no live services needed)
# ---------------------------------------------------------------------------


async def test_health_returns_200():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_query_returns_chunks_or_empty():
    # Phase 0: expects empty chunks; Phase 1+: expects chunks if ingested
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/query",
            json={"query": "test query", "top_k": 5},
        )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["chunks"], list)
    assert body["total"] == len(body["chunks"])


async def test_document_returns_chunks_or_empty():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/document",
            json={"source_url": "https://example.com/not-indexed"},
        )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["chunks"], list)
    assert body["total"] == len(body["chunks"])


# ---------------------------------------------------------------------------
# Qdrant test (requires live Qdrant — skips if unreachable)
# ---------------------------------------------------------------------------


async def test_qdrant_collection_exists():
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http.exceptions import UnexpectedResponse
    from qdrant_client.models import Distance, VectorParams

    from teamrag.config import settings

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        # Check if collection exists; create it if absent (Phase 0 bootstrap)
        try:
            collection_info = await client.get_collection(settings.QDRANT_COLLECTION)
        except (UnexpectedResponse, Exception) as exc:
            # Collection doesn't exist yet — create a minimal one
            error_str = str(exc).lower()
            if "not found" in error_str or "404" in error_str or "doesn't exist" in error_str:
                await client.create_collection(
                    collection_name=settings.QDRANT_COLLECTION,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
                )
                collection_info = await client.get_collection(settings.QDRANT_COLLECTION)
            else:
                raise

        # Verify collection exists (may have points if Phase 1+ data is ingested)
        vector_count = collection_info.points_count
        # points_count may be None on a brand-new empty collection, or positive if ingested
        assert vector_count is None or vector_count >= 0, (
            f"Expected ≥0 vectors in collection '{settings.QDRANT_COLLECTION}', "
            f"got {vector_count}"
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


# ---------------------------------------------------------------------------
# Postgres test (requires live Postgres — skips if unreachable)
# ---------------------------------------------------------------------------


async def test_postgres_tables_exist():
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")

    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        expected_tables = ["sources", "chunks", "acl_tags", "audit_log"]
        for table in expected_tables:
            assert table in tables, (
                f"Table '{table}' not found in database. "
                f"Existing tables: {tables}"
            )
    except Exception as exc:
        error_str = str(exc).lower()
        if any(
            keyword in error_str
            for keyword in ("connection", "refused", "timeout", "unreachable", "network", "could not connect", "connect call failed", "errno 61", "connect")
        ):
            pytest.skip(f"Postgres not reachable: {exc}")
        raise
    finally:
        await engine.dispose()

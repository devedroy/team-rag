"""Phase 5 integration tests — tier-0 ACL filtering (Qdrant + Postgres + API).

Requires Docker stack: Qdrant, TEI, Postgres (per project standard).
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import delete, select

from teamrag.main import app

pytestmark = pytest.mark.asyncio

TIER0_POINT_ID = 9_000_000_000_000
PRIVATE_POINT_ID = 9_000_000_000_001
PROBE_QUERY = "phase5_acl_integration_probe_v1_unique"
SECRET_MARKER = "PHASE5_ACL_SECRET_FORBIDDEN_CONTENT_XYZ"
PUBLIC_MARKER = "PHASE5_ACL_PUBLIC_TIER0_MARKER_XYZ"


async def _ensure_collection(client: AsyncQdrantClient, name: str) -> None:
    try:
        await client.get_collection(name)
    except Exception:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


def _skip_if_unreachable(exc: Exception) -> None:
    error_str = str(exc).lower()
    if any(
        keyword in error_str
        for keyword in (
            "connection",
            "refused",
            "timeout",
            "unreachable",
            "network",
            "errno 61",
        )
    ):
        pytest.skip(str(exc))


async def test_unauthenticated_query_never_returns_non_tier0_chunks():
    """Seeded non–tier-0 points with the same query vector must not appear."""
    from teamrag.config import settings
    from teamrag.retrieval import embed_query_text

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        try:
            await _ensure_collection(client, settings.QDRANT_COLLECTION)
            vector = await embed_query_text(PROBE_QUERY, settings.TEI_URL)

            await client.upsert(
                collection_name=settings.QDRANT_COLLECTION,
                points=[
                    PointStruct(
                        id=TIER0_POINT_ID,
                        vector=vector,
                        payload={
                            "content": f"public doc {PUBLIC_MARKER}",
                            "source_url": "https://example.test/tier0",
                            "page_title": "tier0",
                            "last_updated": "",
                            "chunk_index": 0,
                            "acl_tags": ["tier-0"],
                        },
                    ),
                    PointStruct(
                        id=PRIVATE_POINT_ID,
                        vector=vector,
                        payload={
                            "content": f"secret doc {SECRET_MARKER}",
                            "source_url": "https://example.test/private",
                            "page_title": "private",
                            "last_updated": "",
                            "chunk_index": 0,
                            "acl_tags": ["internal-only"],
                        },
                    ),
                ],
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                response = await http.post(
                    "/query",
                    json={"query": PROBE_QUERY, "top_k": 10},
                )

            assert response.status_code == 200
            body = response.json()
            joined = " ".join(c["content"] for c in body["chunks"])
            assert SECRET_MARKER not in joined
            assert PUBLIC_MARKER in joined or any(
                "tier0" in (c.get("page_title") or "") for c in body["chunks"]
            )
        except UnexpectedResponse as exc:
            _skip_if_unreachable(exc)
            raise
        except Exception as exc:
            _skip_if_unreachable(exc)
            raise
        finally:
            try:
                await client.delete(
                    collection_name=settings.QDRANT_COLLECTION,
                    points_selector=[TIER0_POINT_ID, PRIVATE_POINT_ID],
                )
            except Exception:
                pass
    finally:
        await client.close()


async def test_github_postgres_write_persists_tier0_acl_tags():
    """Ingest writer must persist ``acl_tags`` rows for new chunks."""
    from teamrag.db.models import AclTag, Chunk as ChunkModel, Source
    from teamrag.db.session import get_session
    from teamrag.ingest.github import write_github_to_postgres

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")

    source_url = "https://github.com/teamrag-acl-fixture/phantom/pull/999999001"
    pr = {
        "number": 999999001,
        "title": "acl fixture",
        "base": {"repo": {"full_name": "teamrag-acl-fixture/phantom"}},
        "user": {"login": "fixture-bot"},
        "merged_at": "2020-01-01T00:00:00Z",
    }
    chunks = [
        {
            "chunk_id": "a" * 64,
            "content": "fixture chunk for acl",
            "pr_number": 999999001,
            "pr_title": "acl fixture",
            "author": "fixture-bot",
            "merged_at": "2020-01-01T00:00:00Z",
            "repo": "teamrag-acl-fixture/phantom",
            "source_url": source_url,
            "chunk_index": 0,
            "url": source_url,
            "page_title": "acl fixture",
            "last_updated": "2020-01-01T00:00:00Z",
            "space_key": "teamrag-acl-fixture/phantom",
            "page_id": "teamrag-acl-fixture/phantom:999999001",
        }
    ]

    try:
        async for session in get_session():
            try:
                await write_github_to_postgres(pr, chunks, session)
            except Exception as exc:
                _skip_if_unreachable(exc)
                raise

            res = await session.execute(select(Source).where(Source.source_url == source_url))
            source = res.scalar_one_or_none()
            assert source is not None

            r2 = await session.execute(
                select(AclTag.tag)
                .join(ChunkModel, AclTag.chunk_id == ChunkModel.id)
                .where(ChunkModel.source_id == source.id)
            )
            tags = sorted({row[0] for row in r2.all()})
            assert tags == ["tier-0"]

            await session.execute(delete(Source).where(Source.id == source.id))
            await session.commit()
    except Exception as exc:
        err = str(exc).lower()
        if "connection" in err or "refused" in err or "connect" in err:
            pytest.skip(f"Postgres not reachable: {exc}")
        raise

"""MCP handlers use the gateway; ACL on /query matches unauthenticated tier-0 rules."""

from __future__ import annotations

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from teamrag.main import app
from teamrag.mcp_server.gateway_client import TeamRagGateway
from teamrag.mcp_server.handlers import search_knowledge_handler

pytestmark = pytest.mark.asyncio

TIER0_POINT_ID = 9_000_000_000_002
PRIVATE_POINT_ID = 9_000_000_000_003
PROBE_QUERY = "phase5_mcp_gateway_acl_probe_v1_unique"
SECRET_MARKER = "MCP_GATEWAY_ACL_SECRET_FORBIDDEN_CONTENT_XYZ"
PUBLIC_MARKER = "MCP_GATEWAY_ACL_PUBLIC_TIER0_MARKER_XYZ"


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


async def test_search_knowledge_via_gateway_excludes_non_tier0():
    """MCP path must not return non–tier-0 chunks when gateway uses in-process ASGI."""
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
                            "source_url": "https://example.test/mcp-tier0",
                            "page_title": "mcp-tier0",
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
                            "source_url": "https://example.test/mcp-private",
                            "page_title": "mcp-private",
                            "last_updated": "",
                            "chunk_index": 0,
                            "acl_tags": ["internal-only"],
                        },
                    ),
                ],
            )

            gateway = TeamRagGateway(asgi_app=app)
            chunks = await search_knowledge_handler(PROBE_QUERY, 10, gateway)
            joined = " ".join(c.get("content", "") for c in chunks)
            assert SECRET_MARKER not in joined
            assert PUBLIC_MARKER in joined or any(
                "mcp-tier0" in (c.get("page_title") or "") for c in chunks
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

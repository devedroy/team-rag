"""Shared semantic retrieval (TEI embed + Qdrant) with ACL-aware filtering.

MCP ``search_knowledge`` and HTTP ``POST /query`` should call into this module
so ACL semantics stay unified. ``POST /document`` (and MCP ``get_document``)
apply the same tier-0 rules via ``teamrag.acl`` in the document router.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from teamrag.acl import (
    AclFilterMode,
    log_acl_filter_mode,
    qdrant_filter_for_mode,
    resolve_acl_filter_mode_from_request,
)

if TYPE_CHECKING:
    from fastapi import Request
    from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkHit:
    """One ranked chunk from vector search."""

    content: str
    source_url: str
    page_title: str
    score: float
    payload: dict[str, Any]


async def embed_query_text(query: str, tei_url: str) -> list[float]:
    """Embed a single query string via TEI."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{tei_url.rstrip('/')}/embed",
            json={"inputs": [query]},
        )
        response.raise_for_status()
        embeddings = response.json()
        return embeddings[0]


async def semantic_search(
    *,
    query: str,
    top_k: int,
    tei_url: str,
    qdrant_client: "AsyncQdrantClient",
    collection_name: str,
    request: "Request | None" = None,
    acl_mode: AclFilterMode | None = None,
) -> list[ChunkHit]:
    """Run TEI embedding + Qdrant vector search with ACL filters."""
    if acl_mode is None:
        acl_mode = (
            resolve_acl_filter_mode_from_request(request)
            if request is not None
            else AclFilterMode.UNAUTHENTICATED_TIER_0
        )
    log_acl_filter_mode(acl_mode)
    q_filter = qdrant_filter_for_mode(acl_mode)

    vector = await embed_query_text(query, tei_url)

    results = await qdrant_client.query_points(
        collection_name=collection_name,
        query=vector,
        limit=top_k,
        with_payload=True,
        query_filter=q_filter,
    )

    hits: list[ChunkHit] = []
    for point in results.points:
        payload = point.payload or {}
        hits.append(
            ChunkHit(
                content=payload.get("content", ""),
                source_url=payload.get("source_url", ""),
                page_title=payload.get("page_title", ""),
                score=point.score,
                payload=payload,
            )
        )
    return hits

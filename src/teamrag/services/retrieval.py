"""Shared retrieval logic: embedding via TEI and vector search via Qdrant."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ChunkResult(BaseModel):
    content: str
    source_url: str
    page_title: str
    score: float


async def embed_query(query: str, tei_url: str) -> list[float]:
    """Embed a single query string via TEI."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{tei_url.rstrip('/')}/embed",
            json={"inputs": [query]},
        )
        response.raise_for_status()
        embeddings = response.json()
        return embeddings[0]


async def retrieve_chunks(
    query: str,
    qdrant_client: Any,
    collection: str,
    tei_url: str,
    top_k: int,
) -> list[ChunkResult]:
    """Embed query and search Qdrant; returns empty list on any failure."""
    try:
        vector = await embed_query(query, tei_url)
    except Exception as exc:
        logger.warning("TEI embedding failed during retrieval: %s", exc)
        return []

    try:
        results = await qdrant_client.query_points(
            collection_name=collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant query failed during retrieval: %s", exc)
        return []

    chunks: list[ChunkResult] = []
    for hit in results.points:
        payload = hit.payload or {}
        chunks.append(
            ChunkResult(
                content=payload.get("content", ""),
                source_url=payload.get("source_url", ""),
                page_title=payload.get("page_title", ""),
                score=hit.score,
            )
        )
    return chunks

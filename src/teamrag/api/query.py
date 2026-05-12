"""Query endpoint — real vector search via TEI + Qdrant."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


class ChunkResult(BaseModel):
    content: str
    source_url: str
    page_title: str
    score: float


class QueryResponse(BaseModel):
    chunks: list[ChunkResult] = []
    total: int = 0


async def _embed_query(query: str, tei_url: str) -> list[float]:
    """Embed a single query string via TEI."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{tei_url.rstrip('/')}/embed",
            json={"inputs": [query]},
        )
        response.raise_for_status()
        embeddings = response.json()
        return embeddings[0]


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest, http_request: Request) -> QueryResponse:
    from teamrag.config import settings

    qdrant_client = getattr(http_request.app.state, "qdrant_client", None)
    if qdrant_client is None:
        logger.warning("Qdrant client not available — returning empty results")
        return QueryResponse(chunks=[], total=0)

    try:
        vector = await _embed_query(request.query, settings.TEI_URL)
    except Exception as exc:
        logger.warning("TEI embedding failed: %s — returning empty results", exc)
        return QueryResponse(chunks=[], total=0)

    try:
        results = await qdrant_client.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=vector,
            limit=request.top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant search failed: %s — returning empty results", exc)
        return QueryResponse(chunks=[], total=0)

    chunks = []
    for hit in results:
        payload = hit.payload or {}
        chunks.append(
            ChunkResult(
                content=payload.get("content", ""),
                source_url=payload.get("source_url", ""),
                page_title=payload.get("page_title", ""),
                score=hit.score,
            )
        )

    return QueryResponse(chunks=chunks, total=len(chunks))

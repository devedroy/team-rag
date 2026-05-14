"""Query endpoint — real vector search via TEI + Qdrant with ACL filtering."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

from teamrag.retrieval import semantic_search
from teamrag.services.retrieval import ChunkResult

logger = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


# Re-export ChunkResult so existing imports from this module continue to work.
__all__ = ["ChunkResult", "QueryRequest", "QueryResponse"]


class QueryResponse(BaseModel):
    chunks: list[ChunkResult] = []
    total: int = 0


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest, http_request: Request) -> QueryResponse:
    from teamrag.config import settings

    qdrant_client = getattr(http_request.app.state, "qdrant_client", None)
    if qdrant_client is None:
        qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)

    try:
        hits = await semantic_search(
            query=request.query,
            top_k=request.top_k,
            tei_url=settings.TEI_URL,
            qdrant_client=qdrant_client,
            collection_name=settings.QDRANT_COLLECTION,
            request=http_request,
        )
    except Exception as exc:
        logger.warning("Retrieval failed: %s — returning empty results", exc)
        return QueryResponse(chunks=[], total=0)

    chunks = [
        ChunkResult(
            content=h.content,
            source_url=h.source_url,
            page_title=h.page_title,
            score=float(h.score) if h.score is not None else 0.0,
        )
        for h in hits
    ]

    return QueryResponse(chunks=chunks, total=len(chunks))

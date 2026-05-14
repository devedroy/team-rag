"""Query endpoint — real vector search via TEI + Qdrant."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

from teamrag.services.retrieval import ChunkResult, retrieve_chunks

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
        # If not available in app.state (e.g., during testing), create a temporary client
        qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)

    chunks = await retrieve_chunks(
        query=request.query,
        qdrant_client=qdrant_client,
        collection=settings.QDRANT_COLLECTION,
        tei_url=settings.TEI_URL,
        top_k=request.top_k,
    )

    return QueryResponse(chunks=chunks, total=len(chunks))

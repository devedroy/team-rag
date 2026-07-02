"""Query endpoint — real vector search via TEI + Qdrant with ACL filtering."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

from teamrag.auth import InvalidTokenError, resolve_identity
from teamrag.db.models import AuditLog
from teamrag.db.session import get_session
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
        identity = await resolve_identity(http_request)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        hits = await semantic_search(
            query=request.query,
            top_k=request.top_k,
            tei_url=settings.TEI_URL,
            qdrant_client=qdrant_client,
            collection_name=settings.QDRANT_COLLECTION,
            request=http_request,
            identity=identity,
        )
    except Exception as exc:
        logger.warning("Retrieval failed: %s — returning empty results", exc)
        hits = []

    chunks = [
        ChunkResult(
            content=h.content,
            source_url=h.source_url,
            page_title=h.page_title,
            score=float(h.score) if h.score is not None else 0.0,
        )
        for h in hits
    ]

    caller_id = identity.sub if identity is not None else "anonymous"
    applied = ["tier-0", *(identity.groups if identity is not None else ())]
    try:
        async for session in get_session():
            session.add(
                AuditLog(
                    caller_id=caller_id,
                    query_text=request.query,
                    acl_tags_applied=applied,
                    result_count=len(chunks),
                )
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)

    return QueryResponse(chunks=chunks, total=len(chunks))

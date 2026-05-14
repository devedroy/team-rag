"""Document endpoint — chunks for a given source URL (Qdrant scroll).

Unauthenticated callers only receive points tagged **tier-0** (same as ``POST /query``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from qdrant_client import AsyncQdrantClient

from teamrag.acl import (
    log_acl_filter_mode,
    qdrant_filter_scroll_by_source_url,
    resolve_acl_filter_mode_from_request,
)
from teamrag.api.query import ChunkResult, QueryResponse

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentRequest(BaseModel):
    source_url: str = Field(min_length=1, max_length=8192)

    @field_validator("source_url")
    @classmethod
    def strip_and_nonempty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("source_url must not be empty or whitespace-only")
        return s


def source_url_match_variants(canonical: str) -> list[str]:
    """Distinct payload values to match (exact URL ± trailing slash)."""
    out: list[str] = [canonical]
    if canonical.endswith("/"):
        stripped = canonical.rstrip("/")
        if stripped:
            out.append(stripped)
    else:
        out.append(f"{canonical}/")
    # de-dupe preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


@router.post("/document", response_model=QueryResponse)
async def document(request: DocumentRequest, http_request: Request) -> QueryResponse:
    from teamrag.config import settings

    qdrant_client = getattr(http_request.app.state, "qdrant_client", None)
    if qdrant_client is None:
        qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)

    variants = source_url_match_variants(request.source_url)
    acl_mode = resolve_acl_filter_mode_from_request(http_request)
    log_acl_filter_mode(acl_mode)
    flt = qdrant_filter_scroll_by_source_url(variants, acl_mode)

    collected: list[tuple[int, ChunkResult]] = []
    offset = None

    try:
        while True:
            records, next_offset = await qdrant_client.scroll(
                collection_name=settings.QDRANT_COLLECTION,
                scroll_filter=flt,
                limit=256,
                with_payload=True,
                offset=offset,
            )
            for rec in records:
                p = rec.payload or {}
                idx = int(p.get("chunk_index", 0))
                collected.append(
                    (
                        idx,
                        ChunkResult(
                            content=p.get("content", ""),
                            source_url=p.get("source_url", ""),
                            page_title=p.get("page_title", ""),
                            score=0.0,
                        ),
                    )
                )
            if next_offset is None:
                break
            offset = next_offset
    except Exception as exc:
        logger.warning(
            "Qdrant scroll failed for source_url=%r: %s — returning empty results",
            request.source_url,
            exc,
        )
        return QueryResponse(chunks=[], total=0)

    collected.sort(key=lambda t: t[0])
    chunks = [c for _, c in collected]
    return QueryResponse(chunks=chunks, total=len(chunks))

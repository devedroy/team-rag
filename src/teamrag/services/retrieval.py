"""Shared retrieval logic: embedding via TEI.

Vector search against Qdrant lives in ``teamrag.retrieval.semantic_search``,
which is ACL-filtered. There is deliberately no unfiltered "search Qdrant and
return whatever comes back" helper in this module — that primitive existed
here previously (``retrieve_chunks``) and was a standing risk: any future
caller could import it and bypass ACL enforcement entirely. Do not re-add it;
use ``teamrag.retrieval.semantic_search`` instead.
"""

from __future__ import annotations

import logging

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

"""HTTP client for the TeamRag FastAPI gateway (used by MCP tool handlers)."""

from __future__ import annotations

import logging

import httpx

from teamrag.config import settings

logger = logging.getLogger(__name__)


class TeamRagGateway:
    """Thin async client for POST /query and POST /document."""

    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or settings.TEAMRAG_GATEWAY_URL).rstrip("/")
        self.timeout = timeout

    async def post_query(self, query: str, top_k: int) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/query",
                json={"query": query, "top_k": top_k},
            )
            response.raise_for_status()
            return response.json()

    async def post_document(self, source_url: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/document",
                json={"source_url": source_url},
            )
            response.raise_for_status()
            return response.json()

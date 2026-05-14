"""HTTP client for the TeamRag FastAPI gateway (used by MCP tool handlers).

All tools delegate to the same gateway routes as external HTTP clients.
Retrieval-time ACL (Phase 5: unauthenticated callers see **tier-0** Qdrant points
only) is enforced in FastAPI on ``POST /query`` and ``POST /document`` — the MCP
process does not talk to Qdrant directly, so it cannot bypass those filters.
"""

from __future__ import annotations

from typing import Any

import httpx

from teamrag.config import settings


class TeamRagGateway:
    """Async client for POST /query and POST /document."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 60.0,
        *,
        asgi_app: Any | None = None,
    ) -> None:
        self.base_url = (base_url or settings.TEAMRAG_GATEWAY_URL).rstrip("/")
        self.timeout = timeout
        self._asgi_app = asgi_app

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._asgi_app is not None:
            transport = httpx.ASGITransport(app=self._asgi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://teamrag.test",
                timeout=self.timeout,
            ) as client:
                response = await client.post(path, json=payload)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}{path}", json=payload)
        response.raise_for_status()
        return response.json()

    async def post_query(self, query: str, top_k: int) -> dict[str, Any]:
        return await self._post_json("/query", {"query": query, "top_k": top_k})

    async def post_document(self, source_url: str) -> dict[str, Any]:
        return await self._post_json("/document", {"source_url": source_url})

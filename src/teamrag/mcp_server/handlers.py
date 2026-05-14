"""MCP tool logic (testable without the MCP runtime)."""

from __future__ import annotations

import logging
from typing import Any

from teamrag.config import settings
from teamrag.mcp_server.gateway_client import TeamRagGateway

logger = logging.getLogger(__name__)


def _normalize_chunks(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


async def search_knowledge_handler(
    query: str,
    top_k: int | None,
    gateway: TeamRagGateway,
) -> list[dict[str, Any]]:
    text = query.strip()
    if not text:
        raise ValueError("query must not be empty")

    k = settings.TEAMRAG_QUERY_TOP_K_DEFAULT if top_k is None else top_k
    if k < 1 or k > 100:
        raise ValueError("top_k must be between 1 and 100")

    try:
        body = await gateway.post_query(text, k)
    except Exception as exc:
        logger.exception("Gateway POST /query failed: %s", exc)
        raise RuntimeError(f"Gateway request failed: {exc}") from exc

    return _normalize_chunks(body.get("chunks"))


async def get_document_handler(
    source_url: str,
    gateway: TeamRagGateway,
) -> list[dict[str, Any]]:
    url = source_url.strip()
    if not url:
        raise ValueError("source_url must not be empty")

    try:
        body = await gateway.post_document(url)
    except Exception as exc:
        logger.exception("Gateway POST /document failed: %s", exc)
        raise RuntimeError(f"Gateway request failed: {exc}") from exc

    return _normalize_chunks(body.get("chunks"))

"""FastMCP server definition (tools: search_knowledge, get_document)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from teamrag.config import settings
from teamrag.mcp_server.gateway_client import TeamRagGateway
from teamrag.mcp_server.handlers import get_document_handler, search_knowledge_handler

mcp = FastMCP(
    "teamrag",
    instructions=(
        "TeamRag retrieval over the team's indexed docs and PRs. "
        "Use search_knowledge for semantic search; use get_document to load every chunk "
        "for one source URL (e.g. a Confluence page or GitHub PR)."
    ),
)

_gateway = TeamRagGateway()


@mcp.tool()
async def search_knowledge(query: str, top_k: int | None = None) -> list[dict]:
    """Semantic search across all indexed team content. Returns ranked text chunks with citations."""
    return await search_knowledge_handler(query, top_k, _gateway)


@mcp.tool()
async def get_document(source_url: str) -> list[dict]:
    """Fetch all indexed chunks for a single source URL (full document / PR context)."""
    return await get_document_handler(source_url, _gateway)


def run_stdio() -> None:
    """Run MCP over stdio (default for Cursor / Claude Code local configs)."""
    mcp.run()


def run_sse() -> None:
    """Run MCP over HTTP SSE for remote or shared deployments."""
    mcp.settings.host = settings.MCP_SSE_HOST
    mcp.settings.port = int(settings.MCP_SSE_PORT)
    mcp.run(transport="sse")

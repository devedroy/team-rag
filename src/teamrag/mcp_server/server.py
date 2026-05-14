"""FastMCP server definition (tools: search_knowledge, get_document)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from teamrag.config import settings
from teamrag.mcp_server.gateway_client import TeamRagGateway
from teamrag.mcp_server.handlers import get_document_handler, search_knowledge_handler

mcp = FastMCP(
    "teamrag",
    instructions=(
        "TeamRag retrieval over the team's indexed docs and PRs (via the FastAPI gateway). "
        "Unauthenticated access is limited to **tier-0** public/engineering-wide chunks "
        "— the same rules as POST /query and POST /document. "
        "Use search_knowledge for semantic search; use get_document to load chunks "
        "for one source URL (e.g. a Confluence page or GitHub PR)."
    ),
)

_gateway = TeamRagGateway()


@mcp.tool()
async def search_knowledge(query: str, top_k: int | None = None) -> list[dict]:
    """Semantic search across indexed team content (gateway POST /query; tier-0 ACL for unauthenticated callers)."""
    return await search_knowledge_handler(query, top_k, _gateway)


@mcp.tool()
async def get_document(source_url: str) -> list[dict]:
    """Load chunks for one source URL (gateway POST /document; same tier-0 ACL as search)."""
    return await get_document_handler(source_url, _gateway)


def run_stdio() -> None:
    """Run MCP over stdio (default for Cursor / Claude Code local configs)."""
    mcp.run()


def run_sse() -> None:
    """Run MCP over HTTP SSE for remote or shared deployments."""
    mcp.settings.host = settings.MCP_SSE_HOST
    mcp.settings.port = int(settings.MCP_SSE_PORT)
    mcp.run(transport="sse")

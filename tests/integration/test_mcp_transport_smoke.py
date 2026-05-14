"""Lightweight MCP wiring checks (full IDE sessions are manual; see specs validation)."""

import pytest


@pytest.mark.asyncio
async def test_mcp_registers_search_and_document_tools():
    pytest.importorskip("mcp.server.fastmcp")

    from teamrag.mcp_server.server import mcp

    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "search_knowledge" in names
    assert "get_document" in names

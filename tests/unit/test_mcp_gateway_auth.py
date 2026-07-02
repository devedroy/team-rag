"""TeamRagGateway must forward its bearer token to the gateway."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request

from teamrag.mcp_server.gateway_client import TeamRagGateway

echo_app = FastAPI()


@echo_app.post("/query")
async def echo_query(request: Request):
    return {"auth": request.headers.get("authorization", ""), "chunks": [], "total": 0}


@pytest.mark.asyncio
async def test_gateway_sends_bearer_when_configured():
    gateway = TeamRagGateway(asgi_app=echo_app, bearer_token="tok-123")
    data = await gateway.post_query("q", 1)
    assert data["auth"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_gateway_omits_header_without_token():
    gateway = TeamRagGateway(asgi_app=echo_app, bearer_token=None)
    data = await gateway.post_query("q", 1)
    assert data["auth"] == ""

import pytest

from teamrag.mcp_server.handlers import get_document_handler, search_knowledge_handler


class _FakeGateway:
    def __init__(self, query_body: dict | None = None, document_body: dict | None = None) -> None:
        self.query_body = query_body or {"chunks": [], "total": 0}
        self.document_body = document_body or {"chunks": [], "total": 0}

    async def post_query(self, query: str, top_k: int) -> dict:
        return dict(self.query_body)

    async def post_document(self, source_url: str) -> dict:
        return dict(self.document_body)


@pytest.mark.asyncio
async def test_search_knowledge_maps_gateway_chunks():
    gw = _FakeGateway(
        query_body={
            "chunks": [
                {
                    "content": "hello",
                    "source_url": "https://wiki.example/x",
                    "page_title": "X",
                    "score": 0.9,
                }
            ],
            "total": 1,
        }
    )
    out = await search_knowledge_handler("  my topic  ", None, gw)
    assert len(out) == 1
    assert out[0]["content"] == "hello"
    assert out[0]["source_url"] == "https://wiki.example/x"


@pytest.mark.asyncio
async def test_search_knowledge_rejects_empty_query():
    with pytest.raises(ValueError, match="empty"):
        await search_knowledge_handler("  \t  ", None, _FakeGateway())


@pytest.mark.asyncio
async def test_search_knowledge_rejects_bad_top_k():
    with pytest.raises(ValueError, match="top_k"):
        await search_knowledge_handler("ok", 0, _FakeGateway())
    with pytest.raises(ValueError, match="top_k"):
        await search_knowledge_handler("ok", 101, _FakeGateway())


@pytest.mark.asyncio
async def test_get_document_maps_gateway_chunks():
    gw = _FakeGateway(
        document_body={
            "chunks": [
                {"content": "a", "source_url": "https://gh/x", "page_title": "PR", "score": 0.0},
            ],
            "total": 1,
        }
    )
    out = await get_document_handler(" https://gh/x ", gw)
    assert len(out) == 1
    assert out[0]["page_title"] == "PR"


@pytest.mark.asyncio
async def test_get_document_rejects_empty_url():
    with pytest.raises(ValueError, match="empty"):
        await get_document_handler("   ", _FakeGateway())

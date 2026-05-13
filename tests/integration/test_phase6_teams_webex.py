"""Phase 6 integration — Teams + Webex chat chunks: ingest path, /query, MCP, re-upsert.

Requires Docker stack: Qdrant, TEI, Postgres.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams
from sqlalchemy import delete, select

from teamrag.db.models import AclTag, Chunk, Source
from teamrag.ingest.webex import webex_thread_source_url
from teamrag.main import app

pytestmark = pytest.mark.asyncio

PROBE_QUERY = "phase6_chat_ingest_integration_probe_v1_unique_phrase"
TEAMS_THREAD_URL = "https://teams.microsoft.com/l/test/phase6-thread-ingest-a"
WEBEX_THREAD_URL = webex_thread_source_url(
    "https://app.webex.com", "room-phase6", "phase6-root-webex"
)


def _skip_if_unreachable(exc: Exception) -> None:
    error_str = str(exc).lower()
    if any(
        keyword in error_str
        for keyword in (
            "connection",
            "refused",
            "timeout",
            "unreachable",
            "network",
            "errno 61",
            "multiple exceptions",
        )
    ):
        pytest.skip(str(exc))


async def _ensure_collection(client: AsyncQdrantClient, name: str) -> None:
    try:
        await client.get_collection(name)
    except Exception:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


def _chunk_result_public_shape(c: dict) -> dict:
    return {k: c[k] for k in ("content", "source_url", "page_title") if k in c}


@pytest.fixture
def settings():
    from teamrag.config import settings as s
    return s


async def _cleanup_qdrant_and_postgres(settings, teams_id_hex: str, webex_id_hex: str) -> None:
    from teamrag.db.session import get_session

    t_pid = int(teams_id_hex[:16], 16)
    w_pid = int(webex_id_hex[:16], 16)
    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=[t_pid, w_pid],
        )
    except Exception:
        pass
    finally:
        await client.close()

    try:
        async for session in get_session():
            await session.execute(delete(Source).where(Source.source_url == TEAMS_THREAD_URL))
            await session.execute(delete(Source).where(Source.source_url == WEBEX_THREAD_URL))
            await session.commit()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_query_and_mcp_match_chunkresult_shape_for_teams_and_webex(settings):
    """Seeded Teams + Webex points: /query and MCP search_knowledge return ChunkResult fields."""
    from teamrag.ingest.pipeline import embed_chunks, upsert_to_qdrant, write_chat_thread_to_postgres
    from teamrag.ingest.teams import build_teams_thread_chunk
    from teamrag.ingest.webex import build_webex_thread_chunk
    from teamrag.mcp_server.gateway_client import TeamRagGateway
    from teamrag.mcp_server.handlers import get_document_handler, search_knowledge_handler
    from teamrag.db.session import get_session
    from teamrag.retrieval import embed_query_text

    teams_root = {
        "id": "phase6-root-teams",
        "webUrl": TEAMS_THREAD_URL,
        "createdDateTime": "2024-06-01T10:00:00Z",
        "lastModifiedDateTime": "2024-06-01T10:00:00Z",
        "from": {"user": {"id": "u1", "displayName": "Alice"}},
        "body": {"contentType": "html", "content": f"<p>{PROBE_QUERY} kafka thread</p>"},
        "reactions": [],
    }
    teams_replies = [
        {
            "id": "rep-1",
            "createdDateTime": "2024-06-01T10:15:00Z",
            "from": {"user": {"id": "u2", "displayName": "Bob"}},
            "body": {"contentType": "html", "content": "<p>reply on kafka</p>"},
            "reactions": [],
        }
    ]
    wx_root = {
        "id": "phase6-root-webex",
        "roomId": "room-phase6",
        "created": "2024-06-02T12:00:00.000Z",
        "personId": "p1",
        "personEmail": "alice@example.com",
        "markdown": f"{PROBE_QUERY} webex kafka notes",
    }
    wx_replies = [
        {
            "id": "wx-rep-1",
            "parentId": "phase6-root-webex",
            "created": "2024-06-02T12:10:00.000Z",
            "personId": "p2",
            "personEmail": "bob@example.com",
            "text": "second line kafka",
        }
    ]

    tb = build_teams_thread_chunk(
        root=teams_root,
        replies=teams_replies,
        tenant_id="tenant-phase6",
        channel_id="chan-phase6",
        channel_display_name="Team-Channel-Six",
        skip_no_replies=False,
    )
    wb = build_webex_thread_chunk(
        root=wx_root,
        replies=wx_replies,
        org_id="org-phase6",
        room_id="room-phase6",
        room_title="Webex-Space-Six",
        bot_person_id="bot-off",
        web_base="https://app.webex.com",
        skip_no_replies=False,
    )
    assert tb and wb
    teams_chunk, teams_meta = tb
    webex_chunk, webex_meta = wb

    teams_pid_hex = teams_chunk["chunk_id"]
    webex_pid_hex = webex_chunk["chunk_id"]

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    seeded = False
    try:
        await _ensure_collection(client, settings.QDRANT_COLLECTION)
        await embed_query_text(PROBE_QUERY, settings.TEI_URL)

        teams_chunk["content"] = f"{PROBE_QUERY} teams kafka discussion"
        webex_chunk["content"] = f"{PROBE_QUERY} webex kafka discussion"

        t_vec = await embed_chunks([teams_chunk], settings.TEI_URL)
        w_vec = await embed_chunks([webex_chunk], settings.TEI_URL)
        await upsert_to_qdrant([teams_chunk], t_vec, client, settings.QDRANT_COLLECTION)
        await upsert_to_qdrant([webex_chunk], w_vec, client, settings.QDRANT_COLLECTION)

        async for session in get_session():
            await write_chat_thread_to_postgres(
                source_type="teams", chunk=teams_chunk, chunk_metadata=teams_meta, session=session
            )
            await write_chat_thread_to_postgres(
                source_type="webex", chunk=webex_chunk, chunk_metadata=webex_meta, session=session
            )
        seeded = True

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            qres = await http.post("/query", json={"query": PROBE_QUERY, "top_k": 10})
            assert qres.status_code == 200
            qchunks = qres.json()["chunks"]
            teams_hits = [c for c in qchunks if c.get("source_url") == TEAMS_THREAD_URL]
            webex_hits = [c for c in qchunks if c.get("source_url") == WEBEX_THREAD_URL]
            assert teams_hits, "expected Teams chunk in /query"
            assert webex_hits, "expected Webex chunk in /query"
            for h in teams_hits + webex_hits:
                assert set(h.keys()) == {"content", "source_url", "page_title", "score"}
                assert h["content"]
                assert h["page_title"]

            assert teams_hits[0]["page_title"] == "Team-Channel-Six"
            assert webex_hits[0]["page_title"] == "Webex-Space-Six"

            gateway = TeamRagGateway(asgi_app=app)
            mchunks = await search_knowledge_handler(PROBE_QUERY, 10, gateway)
            q_map = {c["source_url"]: c for c in qchunks}
            m_map = {c["source_url"]: c for c in mchunks}
            for u in (TEAMS_THREAD_URL, WEBEX_THREAD_URL):
                assert u in q_map and u in m_map
                assert _chunk_result_public_shape(m_map[u]) == _chunk_result_public_shape(
                    q_map[u]
                )

            doc = await http.post("/document", json={"source_url": TEAMS_THREAD_URL})
            assert doc.status_code == 200
            dchunks = doc.json()["chunks"]
            assert any(TEAMS_THREAD_URL == c.get("source_url") for c in dchunks)

            wx_doc = await get_document_handler(WEBEX_THREAD_URL, gateway)
            assert any(c.get("source_url") == WEBEX_THREAD_URL for c in wx_doc)

        async for session in get_session():
            r = await session.execute(select(Source).where(Source.source_url == TEAMS_THREAD_URL))
            src = r.scalar_one()
            r2 = await session.execute(select(Chunk).where(Chunk.source_id == src.id))
            ch = r2.scalar_one()
            assert ch.chunk_metadata.get("source") == "teams"
            r3 = await session.execute(select(AclTag).where(AclTag.chunk_id == ch.id))
            tags = [row.tag for row in r3.scalars().all()]
            assert "tier-0" in tags

    except UnexpectedResponse as exc:
        _skip_if_unreachable(exc)
        raise
    except Exception as exc:
        _skip_if_unreachable(exc)
        raise
    finally:
        try:
            await client.close()
        except Exception:
            pass
        if seeded:
            await _cleanup_qdrant_and_postgres(settings, teams_pid_hex, webex_pid_hex)


@pytest.mark.asyncio
async def test_teams_thread_reupsert_same_qdrant_id_updates_metadata(settings):
    """Second ingest of same thread id overwrites Qdrant payload and Postgres row."""
    from teamrag.ingest.pipeline import embed_chunks, upsert_to_qdrant, write_chat_thread_to_postgres
    from teamrag.ingest.teams import build_teams_thread_chunk
    from teamrag.db.session import get_session

    url = "https://teams.microsoft.com/l/test/phase6-reupsert-thread"
    root = {
        "id": "phase6-reupsert-root",
        "webUrl": url,
        "createdDateTime": "2024-07-01T09:00:00Z",
        "lastModifiedDateTime": "2024-07-01T09:00:00Z",
        "from": {"user": {"id": "a1", "displayName": "Ann"}},
        "body": {"contentType": "html", "content": "<p>root</p>"},
        "reactions": [],
    }
    reply1 = {
        "id": "r-a",
        "createdDateTime": "2024-07-01T09:10:00Z",
        "from": {"user": {"id": "a2", "displayName": "Ben"}},
        "body": {"contentType": "html", "content": "<p>one</p>"},
        "reactions": [],
    }

    built1 = build_teams_thread_chunk(
        root=root,
        replies=[reply1],
        tenant_id="t-reup",
        channel_id="c-reup",
        channel_display_name="Reup-Chan",
        skip_no_replies=False,
    )
    assert built1
    chunk1, meta1 = built1
    pid = int(chunk1["chunk_id"][:16], 16)

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    mutated = False
    try:
        await _ensure_collection(client, settings.QDRANT_COLLECTION)
        v1 = await embed_chunks([chunk1], settings.TEI_URL)
        await upsert_to_qdrant([chunk1], v1, client, settings.QDRANT_COLLECTION)
        async for session in get_session():
            await write_chat_thread_to_postgres(
                source_type="teams", chunk=chunk1, chunk_metadata=meta1, session=session
            )
        mutated = True

        reply2 = {
            "id": "r-b",
            "createdDateTime": "2024-07-01T09:20:00Z",
            "from": {"user": {"id": "a3", "displayName": "Cam"}},
            "body": {"contentType": "html", "content": "<p>two</p>"},
            "reactions": [],
        }
        root2 = dict(root)
        root2["lastModifiedDateTime"] = "2024-07-01T09:25:00Z"
        built2 = build_teams_thread_chunk(
            root=root2,
            replies=[reply1, reply2],
            tenant_id="t-reup",
            channel_id="c-reup",
            channel_display_name="Reup-Chan",
            skip_no_replies=False,
        )
        assert built2
        chunk2, meta2 = built2
        assert chunk2["chunk_id"] == chunk1["chunk_id"]
        v2 = await embed_chunks([chunk2], settings.TEI_URL)
        await upsert_to_qdrant([chunk2], v2, client, settings.QDRANT_COLLECTION)
        async for session in get_session():
            await write_chat_thread_to_postgres(
                source_type="teams", chunk=chunk2, chunk_metadata=meta2, session=session
            )

        pts = await client.retrieve(
            collection_name=settings.QDRANT_COLLECTION,
            ids=[pid],
            with_payload=True,
        )
        assert len(pts) == 1
        assert pts[0].payload.get("reply_count") == 2

        async for session in get_session():
            r = await session.execute(select(Source).where(Source.source_url == url))
            src = r.scalar_one()
            r2 = await session.execute(select(Chunk).where(Chunk.source_id == src.id))
            ch = r2.scalar_one()
            assert ch.chunk_metadata.get("reply_count") == 2

    except UnexpectedResponse as exc:
        _skip_if_unreachable(exc)
        raise
    except Exception as exc:
        _skip_if_unreachable(exc)
        raise
    finally:
        try:
            if mutated:
                await client.delete(
                    collection_name=settings.QDRANT_COLLECTION,
                    points_selector=[pid],
                )
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass

    if mutated:
        try:
            async for session in get_session():
                await session.execute(delete(Source).where(Source.source_url == url))
                await session.commit()
        except Exception:
            pass

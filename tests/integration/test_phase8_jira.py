"""Phase 8 integration — Jira ticket chunk: real assembly/chunking, /query citation.

Requires Docker stack: Qdrant, TEI, Postgres.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams

from teamrag.config import EMBEDDING_DIM

pytestmark = pytest.mark.asyncio

JIRA_URL = "https://example.atlassian.net"
ISSUE_URL = f"{JIRA_URL}/browse/ENG-42"


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
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )


def _adf(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]},
        ],
    }


def _resolved_issue() -> dict:
    return {
        "key": "ENG-42",
        "fields": {
            "summary": "Rate limiting at the gateway",
            "description": _adf(
                "Tried token bucket at the gateway. See "
                "https://github.com/org/repo/pull/42"
            ),
            "status": {"name": "Done"},
            "assignee": {"displayName": "Alice"},
            "reporter": {"displayName": "Bob"},
            "labels": ["infra", "gateway"],
            "resolution": {"name": "Fixed"},
            "updated": "2026-07-01T10:00:00.000+0000",
            "parent": {"key": "ENG-10"},
        },
    }


def _comments() -> list[dict]:
    return [
        {
            "author": {"displayName": "Carol"},
            "body": _adf("Worked well in staging."),
        },
    ]


@pytest.fixture
def settings():
    from teamrag.config import settings as s

    return s


@pytest.fixture
async def jira_point(settings):
    """Build the resolved-ticket chunk via the real Jira assembly/chunking
    functions, embed it via TEI, and upsert it into Qdrant. Cleans up the
    point (and any Postgres source row) in teardown.
    """
    from teamrag.ingest.jira import assemble_issue_document, chunk_issue_document
    from teamrag.ingest.pipeline import embed_chunks, upsert_to_qdrant

    issue = _resolved_issue()
    comments = _comments()
    document = assemble_issue_document(issue, comments)
    chunks = chunk_issue_document(issue, document, comments, JIRA_URL)
    assert len(chunks) == 1
    chunk = chunks[0]
    chunk_id_hex = chunk["chunk_id"]
    point_id = int(chunk_id_hex[:16], 16)

    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    seeded = False
    try:
        await _ensure_collection(client, settings.QDRANT_COLLECTION)
        vectors = await embed_chunks([chunk], settings.TEI_URL)
        await upsert_to_qdrant([chunk], vectors, client, settings.QDRANT_COLLECTION)
        seeded = True
        yield chunk, point_id, client
    except UnexpectedResponse as exc:
        _skip_if_unreachable(exc)
        raise
    except Exception as exc:
        _skip_if_unreachable(exc)
        raise
    finally:
        if seeded:
            try:
                await client.delete(
                    collection_name=settings.QDRANT_COLLECTION,
                    points_selector=[point_id],
                )
            except Exception:
                pass
        try:
            await client.close()
        except Exception:
            pass
        try:
            from sqlalchemy import delete

            from teamrag.db.session import get_session
            from teamrag.db.models import Source

            async for session in get_session():
                await session.execute(
                    delete(Source).where(Source.source_url == ISSUE_URL)
                )
                await session.commit()
        except Exception:
            pass


async def test_query_returns_resolved_ticket_with_citation(jira_point):
    """A "have we tried this before?" query surfaces the resolved ticket's
    /browse/ citation — the roadmap Phase 8 "done when" criterion.
    """
    from teamrag.main import app

    _chunk, _point_id, _client = jira_point

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/query",
            json={"query": "have we tried rate-limiting at the gateway before?", "top_k": 10},
        )
    assert response.status_code == 200
    urls = {c["source_url"] for c in response.json()["chunks"]}
    assert ISSUE_URL in urls


async def test_qdrant_payload_has_status_resolution_and_linked_prs(jira_point, settings):
    """Payload metadata carries status/resolution/linked_prs beyond the
    public ChunkResult shape — mirrors test_phase6's payload assertions.
    """
    _chunk, point_id, client = jira_point

    points = await client.retrieve(
        collection_name=settings.QDRANT_COLLECTION,
        ids=[point_id],
        with_payload=True,
    )
    assert len(points) == 1
    payload = points[0].payload
    assert payload.get("source_url") == ISSUE_URL
    assert payload.get("status") == "Done"
    assert payload.get("resolution") == "Fixed"
    assert payload.get("linked_prs")
    assert "https://github.com/org/repo/pull/42" in payload["linked_prs"]
    assert payload.get("issue_key") == "ENG-42"
    assert payload.get("project_key") == "ENG"

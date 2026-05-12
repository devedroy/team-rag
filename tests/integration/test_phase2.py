"""Phase 2 integration tests — GitHub PR ingest pipeline.

All tests skip gracefully when GITHUB_TOKEN is not set in the environment.
Requires Docker stack running: Qdrant, Postgres, TEI.
"""

from __future__ import annotations

import os
import re

import pytest
import pytest_asyncio

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
SKIP_REASON = "GITHUB_TOKEN not set — skipping GitHub integration tests"
pytestmark = pytest.mark.skipif(not GITHUB_TOKEN, reason=SKIP_REASON)


@pytest.fixture(scope="module")
def settings():
    from teamrag.config import settings as _settings
    return _settings


@pytest.fixture(scope="module")
def repos(settings):
    return [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]


@pytest.fixture(scope="module")
def first_repo(repos):
    if not repos or repos[0] in ("org/repo1", "org/repo2"):
        pytest.skip("GITHUB_REPOS not configured with a real repo")
    return repos[0]


@pytest_asyncio.fixture(scope="module")
async def qdrant_client(settings):
    from qdrant_client import AsyncQdrantClient
    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        yield client
    finally:
        await client.close()


async def _run_github_ingest(settings) -> None:
    """Run one-PR GitHub ingest for test purposes."""
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, VectorParams

    from teamrag.db.session import get_session
    from teamrag.ingest.github import (
        GitHubClient,
        assemble_pr_document,
        chunk_pr_document,
        write_github_to_postgres,
    )
    from teamrag.ingest.pipeline import embed_chunks, upsert_to_qdrant

    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        try:
            await qdrant.get_collection(settings.QDRANT_COLLECTION)
        except Exception:
            await qdrant.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        repos = [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]
        async with GitHubClient(settings) as github:
            async for session in get_session():
                for repo in repos:
                    async for pr in github.fetch_merged_prs(repo):
                        pr_number = pr["number"]
                        reviews = await github.fetch_review_comments(repo, pr_number)
                        inline_comments = await github.fetch_inline_comments(repo, pr_number)
                        pr_body = pr.get("body") or ""
                        issue_refs = github._extract_issue_refs(pr_body)
                        issue_bodies: list[str] = []
                        for issue_num in issue_refs:
                            body = await github.fetch_issue_body(repo, issue_num)
                            if body:
                                issue_bodies.append(body)
                        document = assemble_pr_document(pr, reviews, inline_comments, issue_bodies)
                        chunks = chunk_pr_document(pr, document)
                        if not chunks:
                            continue
                        vectors = await embed_chunks(chunks, settings.TEI_URL)
                        await upsert_to_qdrant(chunks, vectors, qdrant, settings.QDRANT_COLLECTION)
                        await write_github_to_postgres(pr, chunks, session)
                        # Only ingest one PR for speed in tests
                        return
    finally:
        await qdrant.close()


@pytest.mark.asyncio
async def test_qdrant_point_count_increases(settings, qdrant_client):
    """Point count after GitHub ingest must be greater than before."""
    from qdrant_client.models import Filter

    # Delete all points to test fresh ingest (idempotent upserts won't increase count)
    try:
        await qdrant_client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=Filter(),
        )
    except Exception:
        pass  # Collection might not exist yet

    info_before = await qdrant_client.get_collection(settings.QDRANT_COLLECTION)
    count_before = info_before.points_count or 0

    await _run_github_ingest(settings)

    info_after = await qdrant_client.get_collection(settings.QDRANT_COLLECTION)
    count_after = info_after.points_count or 0

    assert count_after > count_before, (
        f"Expected point count to increase but got {count_before} → {count_after}"
    )


@pytest.mark.asyncio
async def test_ingest_is_idempotent(settings, qdrant_client):
    """Running ingest twice must not change the point count."""
    await _run_github_ingest(settings)
    info_first = await qdrant_client.get_collection(settings.QDRANT_COLLECTION)
    count_first = info_first.points_count or 0

    await _run_github_ingest(settings)
    info_second = await qdrant_client.get_collection(settings.QDRANT_COLLECTION)
    count_second = info_second.points_count or 0

    assert count_first == count_second, (
        f"Idempotency failed: {count_first} → {count_second} on second run"
    )


@pytest.mark.asyncio
async def test_query_returns_github_chunk(settings):
    """POST /query must return at least one chunk with a github.com source URL."""
    from httpx import ASGITransport, AsyncClient

    from teamrag.main import app

    await _run_github_ingest(settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/query", json={"query": "pull request", "top_k": 10})

    assert response.status_code == 200
    chunks = response.json()["chunks"]
    github_chunks = [c for c in chunks if "github.com" in c.get("source_url", "")]
    assert github_chunks, "Expected at least one chunk with a github.com source_url"


@pytest.mark.asyncio
async def test_chunk_metadata_fields_github(settings):
    """All required metadata fields must be present and non-empty on GitHub chunks."""
    from httpx import ASGITransport, AsyncClient

    from teamrag.main import app

    await _run_github_ingest(settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/query", json={"query": "pull request", "top_k": 10})

    chunks = response.json()["chunks"]
    github_chunks = [c for c in chunks if "github.com" in c.get("source_url", "")]
    assert github_chunks, "No GitHub chunks found — run ingest first"

    required = {"content", "source_url"}
    for chunk in github_chunks:
        missing = required - chunk.keys()
        assert not missing, f"Chunk missing fields: {missing}"
        assert chunk["content"], "content must be non-empty"
        assert chunk["source_url"], "source_url must be non-empty"


@pytest.mark.asyncio
async def test_query_surfaces_pr_citation_url(settings):
    """source_url on GitHub chunks must match the PR URL pattern."""
    from httpx import ASGITransport, AsyncClient

    from teamrag.main import app

    await _run_github_ingest(settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/query", json={"query": "pull request", "top_k": 10})

    chunks = response.json()["chunks"]
    pr_url_pattern = re.compile(r"https://github\.com/[^/]+/[^/]+/pull/\d+")
    github_chunks = [c for c in chunks if "github.com" in c.get("source_url", "")]
    assert github_chunks, "No GitHub chunks found in query results"
    for chunk in github_chunks:
        assert pr_url_pattern.match(chunk["source_url"]), (
            f"source_url does not match PR URL pattern: {chunk['source_url']}"
        )

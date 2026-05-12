"""GitHub REST API client for fetching merged PRs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import AsyncIterator

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class GitHubClient:
    """Async GitHub REST API client.

    Usage::

        async with GitHubClient(settings) as gh:
            async for pr in gh.fetch_merged_prs("org/repo"):
                ...
    """

    def __init__(self, settings) -> None:
        self._token = settings.GITHUB_TOKEN
        self._max_prs = settings.GITHUB_MAX_PRS
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GitHubClient":
        self._http = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def fetch_merged_prs(self, repo: str) -> AsyncIterator[dict]:
        """Yield merged PRs for *repo* in reverse-update order, up to GITHUB_MAX_PRS."""
        url = f"{_GITHUB_API}/repos/{repo}/pulls"
        params: dict = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
            "page": 1,
        }
        count = 0
        while count < self._max_prs:
            pulls = (await self._get(url, params=params)).json()
            if not pulls:
                break
            for pr in pulls:
                if pr.get("merged_at"):
                    yield pr
                    count += 1
                    if count >= self._max_prs:
                        return
            if len(pulls) < 100:
                break
            params["page"] += 1

    async def fetch_review_comments(self, repo: str, pr_number: int) -> list[dict]:
        """Return all review objects for a PR (each has a `body` field)."""
        url = f"{_GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews"
        return (await self._get(url)).json()

    async def fetch_inline_comments(self, repo: str, pr_number: int) -> list[dict]:
        """Return diff-level (inline) comments for a PR."""
        url = f"{_GITHUB_API}/repos/{repo}/pulls/{pr_number}/comments"
        return (await self._get(url)).json()

    async def fetch_issue_body(self, repo: str, issue_number: int) -> str | None:
        """Return the body of a linked issue, or None if not found."""
        url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}"
        try:
            return (await self._get(url)).json().get("body")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def _extract_issue_refs(self, pr_body: str) -> list[int]:
        """Return all #NNN issue numbers referenced in *pr_body*."""
        return [int(m) for m in re.findall(r"#(\d+)", pr_body)]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("GitHubClient must be used as an async context manager")
        response = await self._http.get(url, params=params)
        await self._maybe_sleep_for_rate_limit(response)
        response.raise_for_status()
        return response

    async def _maybe_sleep_for_rate_limit(self, response: httpx.Response) -> None:
        remaining = int(response.headers.get("X-RateLimit-Remaining", "100"))
        if remaining < 5:
            reset_ts = int(response.headers.get("X-RateLimit-Reset", "0"))
            wait = max(0, reset_ts - int(time.time()))
            if wait > 0:
                logger.warning(
                    "GitHub rate limit nearly exhausted (%d remaining); sleeping %ds",
                    remaining, wait,
                )
                await asyncio.sleep(wait)


def assemble_pr_document(
    pr: dict,
    reviews: list[dict],
    inline_comments: list[dict],
    issue_bodies: list[str],
) -> str:
    """Combine all PR content into one queryable document string."""
    parts: list[str] = []

    title = pr.get("title", "")
    body = (pr.get("body") or "").strip()
    if title or body:
        parts.append(f"# {title}\n{body}".strip())

    review_texts = [r.get("body", "").strip() for r in reviews if r.get("body", "").strip()]
    if review_texts:
        parts.append("## Reviews\n" + "\n\n".join(review_texts))

    inline_texts = [c.get("body", "").strip() for c in inline_comments if c.get("body", "").strip()]
    if inline_texts:
        parts.append("## Inline Comments\n" + "\n\n".join(inline_texts))

    nonempty_issues = [b.strip() for b in issue_bodies if b and b.strip()]
    if nonempty_issues:
        parts.append("## Linked Issues\n" + "\n\n".join(nonempty_issues))

    return "\n\n".join(parts)


def chunk_pr_document(pr: dict, document: str) -> list[dict]:
    """Split a PR document into chunks with full metadata."""
    from llama_index.core import Document
    from llama_index.core.node_parser import SentenceSplitter

    repo = pr["base"]["repo"]["full_name"]
    pr_number = pr["number"]
    pr_title = pr.get("title", "")
    author = pr.get("user", {}).get("login", "")
    merged_at = pr.get("merged_at", "")
    source_url = f"https://github.com/{repo}/pull/{pr_number}"

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=50)
    nodes = splitter.get_nodes_from_documents([Document(text=document)])

    chunks: list[dict] = []
    for idx, node in enumerate(nodes):
        text = node.get_content().strip()
        if not text:
            continue
        chunk_id = hashlib.sha256(f"{repo}:{pr_number}:{idx}".encode()).hexdigest()
        chunks.append({
            "chunk_id": chunk_id,
            "content": text,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "author": author,
            "merged_at": merged_at,
            "repo": repo,
            "source_url": source_url,
            "chunk_index": idx,
            # Compat fields for the shared upsert_to_qdrant function
            "url": source_url,
            "page_title": pr_title,
            "last_updated": merged_at,
            "space_key": repo,
            "page_id": f"{repo}:{pr_number}",
        })

    logger.info("PR #%d chunked into %d chunks", pr_number, len(chunks))
    return chunks


async def write_github_to_postgres(pr: dict, chunks: list[dict], session) -> None:
    """Upsert one Source row and one Chunk row per chunk into Postgres."""
    from datetime import datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.db.models import Chunk as ChunkModel
    from teamrag.db.models import Source

    repo = pr["base"]["repo"]["full_name"]
    pr_number = pr["number"]
    pr_title = pr.get("title", "")
    source_url = f"https://github.com/{repo}/pull/{pr_number}"

    merged_at_str = pr.get("merged_at") or ""
    merged_at = None
    if merged_at_str:
        try:
            merged_at = datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    stmt = (
        pg_insert(Source)
        .values(
            source_type="github",
            source_url=source_url,
            page_title=pr_title,
            last_updated=merged_at,
        )
        .on_conflict_do_update(
            index_elements=["source_url"],
            set_={"page_title": pr_title, "last_updated": merged_at},
        )
        .returning(Source.id)
    )
    result = await session.execute(stmt)
    source_id = result.scalar_one()

    for chunk in chunks:
        chunk_stmt = (
            pg_insert(ChunkModel)
            .values(
                source_id=source_id,
                content=chunk["content"],
                chunk_index=chunk["chunk_index"],
                chunk_metadata={
                    "pr_number": chunk["pr_number"],
                    "author": chunk["author"],
                    "repo": chunk["repo"],
                    "merged_at": chunk["merged_at"],
                },
            )
            .on_conflict_do_update(
                constraint="uq_chunks_source_chunk_index",
                set_={"content": chunk["content"]},
            )
        )
        await session.execute(chunk_stmt)

    await session.commit()
    logger.info("Wrote source + %d chunks to Postgres for PR %s#%d", len(chunks), repo, pr_number)

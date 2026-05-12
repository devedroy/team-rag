"""CLI entry point: python -m teamrag.ingest <confluence|github>"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _run_confluence() -> None:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, VectorParams

    from teamrag.config import settings
    from teamrag.db.session import get_session

    missing = [
        var for var, val in [
            ("CONFLUENCE_URL", settings.CONFLUENCE_URL),
            ("CONFLUENCE_USERNAME", settings.CONFLUENCE_USERNAME),
            ("CONFLUENCE_API_TOKEN", settings.CONFLUENCE_API_TOKEN),
            ("CONFLUENCE_SPACE_KEYS", settings.CONFLUENCE_SPACE_KEYS),
        ]
        if not val or val in ("https://your-org.atlassian.net", "you@org.com", "your-token-here")
    ]
    if missing:
        logger.error(
            "Missing or unconfigured Confluence credentials: %s. Set these in .env and retry.",
            ", ".join(missing),
        )
        sys.exit(1)
    from teamrag.ingest.confluence import ConfluenceClient
    from teamrag.ingest.pipeline import chunk_document, embed_chunks, upsert_to_qdrant, write_to_postgres

    confluence = ConfluenceClient(settings)

    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        try:
            await qdrant.get_collection(settings.QDRANT_COLLECTION)
        except Exception:
            logger.info("Creating Qdrant collection '%s'", settings.QDRANT_COLLECTION)
            await qdrant.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        pages_processed = 0
        chunks_total = 0

        async for session in get_session():
            async for page in confluence.fetch_all_spaces():
                chunks = chunk_document(page, settings.CONFLUENCE_URL)
                if not chunks:
                    continue

                vectors = await embed_chunks(chunks, settings.TEI_URL)
                await upsert_to_qdrant(chunks, vectors, qdrant, settings.QDRANT_COLLECTION)
                await write_to_postgres(page, chunks, session, settings.CONFLUENCE_URL)

                pages_processed += 1
                chunks_total += len(chunks)
                logger.info(
                    "Progress: %d pages, %d chunks so far",
                    pages_processed,
                    chunks_total,
                )

        logger.info(
            "Ingestion complete: %d pages processed, %d chunks indexed",
            pages_processed,
            chunks_total,
        )
    finally:
        await qdrant.close()


async def _run_github() -> None:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, VectorParams

    from teamrag.config import settings
    from teamrag.db.session import get_session

    missing = [
        var for var, val in [
            ("GITHUB_TOKEN", settings.GITHUB_TOKEN),
            ("GITHUB_REPOS", settings.GITHUB_REPOS),
        ]
        if not val or val in ("ghp_your_token_here", "org/repo1,org/repo2")
    ]
    if missing:
        logger.error(
            "Missing or unconfigured GitHub credentials: %s. Set these in .env and retry.",
            ", ".join(missing),
        )
        sys.exit(1)

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
            logger.info("Creating Qdrant collection '%s'", settings.QDRANT_COLLECTION)
            await qdrant.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        repos = [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]
        repos_processed = 0
        prs_processed = 0
        chunks_total = 0

        async with GitHubClient(settings) as github:
            async for session in get_session():
                for repo in repos:
                    logger.info("Starting ingest for repo: %s", repo)
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

                        prs_processed += 1
                        chunks_total += len(chunks)
                        logger.info(
                            "Repo %s: PR #%d → %d chunks (total so far: %d)",
                            repo, pr_number, len(chunks), chunks_total,
                        )

                    repos_processed += 1

        logger.info(
            "GitHub ingest complete: %d repos, %d PRs processed, %d chunks indexed",
            repos_processed,
            prs_processed,
            chunks_total,
        )
    finally:
        await qdrant.close()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m teamrag.ingest <source>")
        print("  Sources: confluence, github")
        sys.exit(1)

    source = sys.argv[1].lower()
    if source == "confluence":
        asyncio.run(_run_confluence())
    elif source == "github":
        asyncio.run(_run_github())
    else:
        logger.error("Unknown source: %s. Valid sources: confluence, github", source)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""CLI entry point: python -m teamrag.ingest <confluence|github|teams|webex|chat> [--poll]"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _ensure_qdrant_collection(qdrant, settings) -> None:
    from qdrant_client.models import Distance, VectorParams

    try:
        await qdrant.get_collection(settings.QDRANT_COLLECTION)
    except Exception:
        logger.info("Creating Qdrant collection '%s'", settings.QDRANT_COLLECTION)
        await qdrant.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


async def _run_confluence() -> None:
    from qdrant_client import AsyncQdrantClient

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
        await _ensure_qdrant_collection(qdrant, settings)

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
        await _ensure_qdrant_collection(qdrant, settings)

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


async def _run_teams_once() -> None:
    from qdrant_client import AsyncQdrantClient

    from teamrag.config import settings
    from teamrag.db.session import get_session
    from teamrag.ingest.teams import TeamsGraphClient, ingest_teams_channels

    missing = [
        var
        for var, val in [
            ("TEAMS_TENANT_ID", settings.TEAMS_TENANT_ID),
            ("TEAMS_CLIENT_ID", settings.TEAMS_CLIENT_ID),
            ("TEAMS_CLIENT_SECRET", settings.TEAMS_CLIENT_SECRET),
            ("TEAMS_BOT_USER_ID", settings.TEAMS_BOT_USER_ID),
        ]
        if not val
    ]
    if missing:
        logger.error(
            "Missing Teams Graph configuration: %s. Set in .env — see docs/ingest-teams-webex.md.",
            ", ".join(missing),
        )
        sys.exit(1)

    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await _ensure_qdrant_collection(qdrant, settings)
        async for session in get_session():
            async with TeamsGraphClient(settings) as graph:
                n = await ingest_teams_channels(
                    settings, graph, session, qdrant, settings.QDRANT_COLLECTION
                )
                logger.info("Teams ingest complete: %d thread chunks", n)
    finally:
        await qdrant.close()


async def _run_webex_once() -> None:
    from qdrant_client import AsyncQdrantClient

    from teamrag.config import settings
    from teamrag.db.session import get_session
    from teamrag.ingest.webex import WebexClient, ingest_webex_spaces

    missing = [
        var
        for var, val in [
            ("WEBEX_BOT_TOKEN", settings.WEBEX_BOT_TOKEN),
            ("WEBEX_ORG_ID", settings.WEBEX_ORG_ID),
        ]
        if not val
    ]
    if missing:
        logger.error(
            "Missing Webex configuration: %s. Set in .env — see docs/ingest-teams-webex.md.",
            ", ".join(missing),
        )
        sys.exit(1)

    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await _ensure_qdrant_collection(qdrant, settings)
        async for session in get_session():
            async with WebexClient(settings) as wx:
                n = await ingest_webex_spaces(
                    settings, wx, session, qdrant, settings.QDRANT_COLLECTION
                )
                logger.info("Webex ingest complete: %d thread chunks", n)
    finally:
        await qdrant.close()


async def _run_chat_once() -> None:
    await _run_teams_once()
    await _run_webex_once()


async def _poll_loop(coro_factory, label: str) -> None:
    from teamrag.config import settings

    interval = max(30, int(settings.CHAT_INGEST_POLL_INTERVAL_SECONDS))
    while True:
        logger.info("Starting %s poll cycle", label)
        try:
            await coro_factory()
        except Exception:
            logger.exception("%s poll cycle failed", label)
        logger.info("Sleeping %ds until next %s poll", interval, label)
        await asyncio.sleep(interval)


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--poll"]
    poll = "--poll" in sys.argv
    if not argv:
        print("Usage: python -m teamrag.ingest <source> [--poll]")
        print("  Sources: confluence, github, teams, webex, chat")
        print("  --poll  Run Teams/Webex/chat on CHAT_INGEST_POLL_INTERVAL_SECONDS (teams/webex/chat only).")
        sys.exit(1)

    source = argv[0].lower()
    if source == "confluence":
        asyncio.run(_run_confluence())
    elif source == "github":
        asyncio.run(_run_github())
    elif source == "teams":
        if poll:
            asyncio.run(_poll_loop(_run_teams_once, "teams"))
        else:
            asyncio.run(_run_teams_once())
    elif source == "webex":
        if poll:
            asyncio.run(_poll_loop(_run_webex_once, "webex"))
        else:
            asyncio.run(_run_webex_once())
    elif source == "chat":
        if poll:
            asyncio.run(_poll_loop(_run_chat_once, "chat"))
        else:
            asyncio.run(_run_chat_once())
    else:
        logger.error(
            "Unknown source: %s. Valid sources: confluence, github, teams, webex, chat",
            source,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

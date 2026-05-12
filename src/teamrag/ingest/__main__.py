"""CLI entry point: python -m teamrag.ingest confluence"""

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
    from teamrag.ingest.confluence import ConfluenceClient
    from teamrag.ingest.pipeline import chunk_document, embed_chunks, upsert_to_qdrant, write_to_postgres

    confluence = ConfluenceClient(settings)

    async with AsyncQdrantClient(url=settings.QDRANT_URL) as qdrant:
        # Ensure collection exists
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


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m teamrag.ingest <source>")
        print("  Sources: confluence")
        sys.exit(1)

    source = sys.argv[1].lower()
    if source == "confluence":
        asyncio.run(_run_confluence())
    else:
        logger.error("Unknown source: %s. Valid sources: confluence", source)
        sys.exit(1)


if __name__ == "__main__":
    main()

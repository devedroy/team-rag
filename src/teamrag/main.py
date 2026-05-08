"""TeamRag FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from teamrag.api.health import router as health_router
from teamrag.api.query import router as query_router
from teamrag.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    On startup: attempt to ping Qdrant. Logs a WARNING if unreachable but
    does not prevent the app from starting.
    """
    try:
        async with AsyncQdrantClient(url=settings.QDRANT_URL) as client:
            await client.get_collections()
        logger.info("Qdrant is reachable at %s", settings.QDRANT_URL)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Qdrant is not reachable at %s — continuing without vector store: %s",
            settings.QDRANT_URL,
            exc,
        )

    yield  # application runs here

    # Shutdown: nothing to clean up in Phase 0


app = FastAPI(
    title="TeamRag",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router, prefix="")
app.include_router(query_router, prefix="")

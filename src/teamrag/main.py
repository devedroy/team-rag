"""TeamRag FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from teamrag.api.document import router as document_router
from teamrag.api.health import router as health_router
from teamrag.api.query import router as query_router
from teamrag.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    On startup: connect to Qdrant and store client on app.state.
    On shutdown: close the Qdrant client.
    """
    qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await qdrant_client.get_collections()
        logger.info("Qdrant is reachable at %s", settings.QDRANT_URL)
        app.state.qdrant_client = qdrant_client
    except Exception as exc:
        logger.warning(
            "Qdrant is not reachable at %s — continuing without vector store: %s",
            settings.QDRANT_URL,
            exc,
        )
        app.state.qdrant_client = None

    yield

    try:
        await qdrant_client.close()
    except Exception as exc:
        logger.warning("Error closing Qdrant client: %s", exc)


app = FastAPI(
    title="TeamRag",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router, prefix="")
app.include_router(query_router, prefix="")
app.include_router(document_router, prefix="")

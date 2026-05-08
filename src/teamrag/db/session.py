"""Async SQLAlchemy session factory for TeamRag."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.teamrag.config import settings

# Engine is created at module import time, but no real connection is made until
# the first query is executed (lazy connect behaviour of SQLAlchemy).
engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a single AsyncSession per request."""
    async with AsyncSessionLocal() as session:
        yield session

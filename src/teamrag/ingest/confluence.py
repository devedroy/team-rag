"""Async Confluence REST API client."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from teamrag.config import Settings

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRY_STATUS


class ConfluenceClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.CONFLUENCE_URL.rstrip("/")
        self._auth = (settings.CONFLUENCE_USERNAME, settings.CONFLUENCE_API_TOKEN)
        self._max_pages = settings.CONFLUENCE_MAX_PAGES
        self._space_keys = [k.strip() for k in settings.CONFLUENCE_SPACE_KEYS.split(",") if k.strip()]

    async def fetch_pages(self, space_key: str) -> AsyncIterator[dict]:
        """Yield raw page dicts from a Confluence space (paginates automatically)."""
        limit = 50
        start = 0
        fetched = 0

        async with httpx.AsyncClient(auth=self._auth, timeout=30.0) as client:
            while True:
                if fetched >= self._max_pages:
                    logger.info("Reached CONFLUENCE_MAX_PAGES=%d for space %s", self._max_pages, space_key)
                    break

                data = await self._get_page(
                    client,
                    f"{self._base_url}/wiki/rest/api/content",
                    params={
                        "spaceKey": space_key,
                        "type": "page",
                        "status": "current",
                        "expand": "body.storage,version,metadata.labels",
                        "limit": limit,
                        "start": start,
                    },
                )
                results = data.get("results", [])
                if not results:
                    break

                for page in results:
                    if fetched >= self._max_pages:
                        break
                    yield page
                    fetched += 1

                if len(results) < limit:
                    break
                start += limit

        logger.info("Fetched %d pages from space %s", fetched, space_key)

    async def fetch_all_spaces(self) -> AsyncIterator[dict]:
        """Yield pages from all configured space keys."""
        for space_key in self._space_keys:
            logger.info("Fetching pages from Confluence space: %s", space_key)
            async for page in self.fetch_pages(space_key):
                yield page

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    async def _get_page(self, client: httpx.AsyncClient, url: str, params: dict) -> dict:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

"""Microsoft Teams channel thread ingestion via Microsoft Graph (application permissions).

Phase 6: backfill + polling only; channels the bot can access; full-thread chunks.
See ``docs/ingest-teams-webex.md`` for Entra app registration and required scopes.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from teamrag.acl import TIER_0_TAG
from teamrag.ingest.chat_signal import (
    channel_meets_min_participants,
    distinct_human_author_ids_teams,
    root_is_bot_teams,
    skip_thread_no_replies,
)
from teamrag.ingest.chat_thread import (
    ChatThreadChunkMetadata,
    build_thread_document,
    deterministic_chat_chunk_id,
    graph_sender_display_name,
    strip_teams_message_body_to_text,
    teams_message_is_from_bot_or_app,
    thread_last_activity,
)

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class TeamsGraphClient:
    """Minimal Graph client for joined teams, channels, messages, and replies."""

    def __init__(self, settings: Any) -> None:
        self._tenant = settings.TEAMS_TENANT_ID
        self._client_id = settings.TEAMS_CLIENT_ID
        self._client_secret = settings.TEAMS_CLIENT_SECRET
        self._bot_user_id = settings.TEAMS_BOT_USER_ID
        self._max_roots = settings.CHAT_INGEST_MAX_CHANNEL_ROOTS
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None

    async def __aenter__(self) -> "TeamsGraphClient":
        self._http = httpx.AsyncClient(timeout=60.0)
        self._token = await _fetch_graph_token(
            self._http, self._tenant, self._client_id, self._client_secret
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._token = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def _get(self, url: str) -> dict[str, Any]:
        if self._http is None or not self._token:
            raise RuntimeError("TeamsGraphClient used outside context manager")
        r = await self._http.get(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        r.raise_for_status()
        return r.json()

    async def joined_teams(self) -> list[dict[str, Any]]:
        data = await self._get(f"{_GRAPH}/users/{self._bot_user_id}/joinedTeams")
        return list(data.get("value", []))

    async def team_channels(self, team_id: str) -> list[dict[str, Any]]:
        data = await self._get(f"{_GRAPH}/teams/{team_id}/channels")
        return list(data.get("value", []))

    async def iter_channel_root_messages(
        self, team_id: str, channel_id: str
    ) -> Any:
        """Yield root channel messages up to CHAT_INGEST_MAX_CHANNEL_ROOTS."""
        url = f"{_GRAPH}/teams/{team_id}/channels/{channel_id}/messages"
        count = 0
        while url and count < self._max_roots:
            payload = await self._get(url)
            for m in payload.get("value", []):
                if count >= self._max_roots:
                    return
                yield m
                count += 1
            url = payload.get("@odata.nextLink")

    async def fetch_replies(
        self, team_id: str, channel_id: str, message_id: str
    ) -> list[dict[str, Any]]:
        url = (
            f"{_GRAPH}/teams/{team_id}/channels/{channel_id}/messages/"
            f"{message_id}/replies"
        )
        replies: list[dict[str, Any]] = []
        while url:
            payload = await self._get(url)
            replies.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
        replies.sort(key=lambda m: m.get("createdDateTime") or "")
        return replies


async def _fetch_graph_token(
    http: httpx.AsyncClient, tenant_id: str, client_id: str, client_secret: str
) -> str:
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    r = await http.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def _teams_body_extractor(msg: dict[str, Any]) -> tuple[str, bool]:
    return strip_teams_message_body_to_text(msg.get("body"))


def build_teams_thread_chunk(
    *,
    root: dict[str, Any],
    replies: list[dict[str, Any]],
    tenant_id: str,
    channel_id: str,
    channel_display_name: str,
    skip_no_replies: bool,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Build (chunk_dict, chunk_metadata_json) or None if filtered out."""
    if root_is_bot_teams(root):
        return None

    human_replies = [r for r in replies if not teams_message_is_from_bot_or_app(r)]
    ordered = [root] + human_replies

    text, has_code, reply_count, reaction_count = build_thread_document(
        ordered,
        platform="teams",
        author_label=graph_sender_display_name,
        body_extractor=_teams_body_extractor,
    )

    if skip_thread_no_replies(reply_count, skip_no_replies):
        return None

    if not text.strip():
        return None

    thread_id = str(root.get("id") or "")
    if not thread_id:
        return None

    source_url = str(root.get("webUrl") or "").strip()
    if not source_url:
        return None

    last_dt = thread_last_activity(ordered, platform="teams")
    if last_dt is None:
        from datetime import datetime, timezone

        last_dt = datetime.now(timezone.utc)
    last_iso = last_dt.isoformat()

    participants: list[str] = []
    seen: set[str] = set()
    for m in ordered:
        if teams_message_is_from_bot_or_app(m):
            continue
        name = graph_sender_display_name(m)
        if name and name not in seen:
            seen.add(name)
            participants.append(name)

    meta = ChatThreadChunkMetadata(
        source="teams",
        tenant_id=tenant_id,
        org_id="",
        channel_id=channel_id,
        space_id="",
        channel_name=channel_display_name,
        space_title="",
        thread_id=thread_id,
        participants=participants,
        reply_count=reply_count,
        reaction_count=reaction_count,
        has_code_block=has_code,
        last_activity_at=last_dt,
        source_url=source_url,
    )
    chunk_id = deterministic_chat_chunk_id(
        "teams", tenant_id, channel_id, thread_id
    )

    chunk_dict: dict[str, Any] = {
        "chunk_id": chunk_id,
        "content": text,
        "chunk_index": 0,
        "url": source_url,
        "source_url": source_url,
        "page_title": meta.page_title,
        "last_updated": last_iso,
        "last_activity_at": last_iso,
        "acl_tags": [TIER_0_TAG],
        "source": "teams",
        "tenant_id": tenant_id,
        "org_id": "",
        "channel_id": channel_id,
        "space_id": "",
        "channel_name": channel_display_name,
        "space_title": "",
        "thread_id": thread_id,
        "participants": participants,
        "reply_count": reply_count,
        "reaction_count": reaction_count,
        "has_code_block": has_code,
    }

    return chunk_dict, meta.to_chunk_metadata_dict()


async def ingest_teams_channels(
    settings: Any,
    graph: TeamsGraphClient,
    session: Any,
    qdrant: Any,
    collection_name: str,
) -> int:
    """Enumerate teams/channels and upsert thread chunks. Returns chunk count."""
    from teamrag.ingest.pipeline import embed_chunks, upsert_to_qdrant, write_chat_thread_to_postgres

    tenant_id = settings.TEAMS_TENANT_ID
    written = 0

    teams = await graph.joined_teams()
    logger.info("Teams ingest: %d joined teams", len(teams))

    for team in teams:
        team_id = str(team.get("id") or "")
        if not team_id:
            continue
        channels = await graph.team_channels(team_id)
        for ch in channels:
            if ch.get("isArchived"):
                continue
            channel_id = str(ch.get("id") or "")
            display = str(ch.get("displayName") or channel_id)
            roots: list[dict[str, Any]] = []
            async for root in graph.iter_channel_root_messages(team_id, channel_id):
                roots.append(root)

            if not roots:
                continue

            if not channel_meets_min_participants(
                len(distinct_human_author_ids_teams(roots)),
                settings.CHAT_INGEST_MIN_PARTICIPANTS,
            ):
                logger.info(
                    "Skipping channel %s / %s: distinct human authors below threshold",
                    team_id,
                    channel_id,
                )
                continue

            for root in roots:
                mid = str(root.get("id") or "")
                if not mid:
                    continue
                replies = await graph.fetch_replies(team_id, channel_id, mid)
                built = build_teams_thread_chunk(
                    root=root,
                    replies=replies,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    channel_display_name=display,
                    skip_no_replies=settings.CHAT_INGEST_SKIP_NO_REPLIES,
                )
                if not built:
                    continue
                chunk_dict, meta_json = built
                vectors = await embed_chunks([chunk_dict], settings.TEI_URL)
                await upsert_to_qdrant([chunk_dict], vectors, qdrant, collection_name)
                await write_chat_thread_to_postgres(
                    source_type="teams",
                    chunk=chunk_dict,
                    chunk_metadata=meta_json,
                    session=session,
                )
                written += 1

    return written

"""Webex Messaging space thread ingestion (group rooms the bot is in).

Phase 6: backfill + polling only; ``docs/ingest-teams-webex.md`` for bot setup.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from teamrag.acl import TIER_0_TAG
from teamrag.ingest.chat_signal import (
    channel_meets_min_participants,
    root_is_bot_webex,
    skip_thread_no_replies,
)
from teamrag.ingest.chat_thread import (
    ChatThreadChunkMetadata,
    build_thread_document,
    deterministic_chat_chunk_id,
    strip_webex_message_to_text,
    thread_last_activity,
    webex_message_is_from_bot,
    webex_sender_display_name,
)

logger = logging.getLogger(__name__)

_WEBEX = "https://webexapis.com/v1"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class WebexClient:
    """Minimal Webex REST client for rooms, messages, memberships, and /people/me."""

    def __init__(self, settings: Any) -> None:
        self._token = settings.WEBEX_BOT_TOKEN
        self._org_id = settings.WEBEX_ORG_ID
        self._web_base = getattr(
            settings, "WEBEX_WEB_CLIENT_BASE", "https://app.webex.com"
        ).rstrip("/")
        self._max_messages = getattr(settings, "CHAT_INGEST_WEBEX_MAX_MESSAGES", 500)
        self._http: httpx.AsyncClient | None = None
        self.bot_person_id: str = ""

    async def __aenter__(self) -> "WebexClient":
        self._http = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        me = await self._get_json(f"{_WEBEX}/people/me")
        self.bot_person_id = str(me.get("id") or "")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("WebexClient used outside context manager")
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def list_group_rooms(self) -> list[dict[str, Any]]:
        rooms: list[dict[str, Any]] = []
        url = f"{_WEBEX}/rooms"
        params: dict[str, Any] | None = {"type": "group", "max": 100}
        while url:
            data = await self._get_json(url, params=params)
            rooms.extend(data.get("items", []))
            next_url = (data.get("links") or {}).get("next")
            if not next_url:
                break
            url = next_url
            params = None
        return rooms

    async def room_membership_person_ids(self, room_id: str) -> set[str]:
        """Distinct personIds in the room (for participant threshold)."""
        ids: set[str] = set()
        url = f"{_WEBEX}/memberships"
        params: dict[str, Any] | None = {"roomId": room_id, "max": 100}
        while url:
            data = await self._get_json(url, params=params)
            for m in data.get("items", []):
                pid = (m.get("personId") or "").strip()
                if pid:
                    ids.add(pid)
            next_url = (data.get("links") or {}).get("next")
            if not next_url:
                break
            url = next_url
            params = None
        return ids

    async def list_room_messages(self, room_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        url = f"{_WEBEX}/messages"
        params: dict[str, Any] | None = {"roomId": room_id, "max": 100}
        fetched = 0
        while url and fetched < self._max_messages:
            data = await self._get_json(url, params=params)
            batch = data.get("items", [])
            messages.extend(batch)
            fetched += len(batch)
            next_url = (data.get("links") or {}).get("next")
            if not next_url:
                break
            url = next_url
            params = None
        return messages


def webex_thread_source_url(web_base: str, room_id: str, root_id: str) -> str:
    """Deep link to the root message in a space (Webex web client URL shape)."""
    return f"{web_base.rstrip('/')}/spaces/{room_id}/messages/{root_id}"


def build_webex_thread_chunk(
    *,
    root: dict[str, Any],
    replies: list[dict[str, Any]],
    org_id: str,
    room_id: str,
    room_title: str,
    bot_person_id: str,
    web_base: str,
    skip_no_replies: bool,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if root_is_bot_webex(root, bot_person_id):
        return None

    human_replies = [r for r in replies if not webex_message_is_from_bot(r, bot_person_id)]
    ordered = [root] + sorted(
        human_replies, key=lambda m: m.get("created") or ""
    )

    text, has_code, reply_count, reaction_count = build_thread_document(
        ordered,
        platform="webex",
        author_label=webex_sender_display_name,
        body_extractor=strip_webex_message_to_text,
    )

    if skip_thread_no_replies(reply_count, skip_no_replies):
        return None

    if not text.strip():
        return None

    thread_id = str(root.get("id") or "")
    if not thread_id:
        return None

    source_url = webex_thread_source_url(web_base, room_id, thread_id)

    last_dt = thread_last_activity(ordered, platform="webex")
    if last_dt is None:
        from datetime import datetime, timezone

        last_dt = datetime.now(timezone.utc)
    last_iso = last_dt.isoformat()

    participants: list[str] = []
    seen: set[str] = set()
    for m in ordered:
        if webex_message_is_from_bot(m, bot_person_id):
            continue
        name = webex_sender_display_name(m)
        if name and name not in seen:
            seen.add(name)
            participants.append(name)

    meta = ChatThreadChunkMetadata(
        source="webex",
        tenant_id="",
        org_id=org_id,
        channel_id="",
        space_id=room_id,
        channel_name="",
        space_title=room_title,
        thread_id=thread_id,
        participants=participants,
        reply_count=reply_count,
        reaction_count=reaction_count,
        has_code_block=has_code,
        last_activity_at=last_dt,
        source_url=source_url,
    )
    chunk_id = deterministic_chat_chunk_id("webex", org_id, room_id, thread_id)

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
        "source": "webex",
        "tenant_id": "",
        "org_id": org_id,
        "channel_id": "",
        "space_id": room_id,
        "channel_name": "",
        "space_title": room_title,
        "thread_id": thread_id,
        "participants": participants,
        "reply_count": reply_count,
        "reaction_count": reaction_count,
        "has_code_block": has_code,
    }

    return chunk_dict, meta.to_chunk_metadata_dict()


def group_webex_messages_by_thread(messages: list[dict[str, Any]]) -> dict[str | None, list[dict[str, Any]]]:
    """Group messages: key None = roots (parentId absent), else parentId."""
    roots: list[dict[str, Any]] = []
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in messages:
        parent = m.get("parentId")
        if not parent:
            roots.append(m)
        else:
            by_parent[str(parent)].append(m)
    out: dict[str | None, list[dict[str, Any]]] = {None: roots}
    for k, v in by_parent.items():
        out[k] = v
    return out


async def ingest_webex_spaces(
    settings: Any,
    wx: WebexClient,
    session: Any,
    qdrant: Any,
    collection_name: str,
) -> int:
    from teamrag.ingest.pipeline import embed_chunks, upsert_to_qdrant, write_chat_thread_to_postgres

    org_id = settings.WEBEX_ORG_ID
    written = 0
    rooms = await wx.list_group_rooms()
    logger.info("Webex ingest: %d group rooms", len(rooms))

    for room in rooms:
        room_id = str(room.get("id") or "")
        title = str(room.get("title") or room_id)
        if not room_id:
            continue

        member_ids = await wx.room_membership_person_ids(room_id)
        human_members = {p for p in member_ids if p and p != wx.bot_person_id}
        if not channel_meets_min_participants(
            len(human_members), settings.CHAT_INGEST_MIN_PARTICIPANTS
        ):
            logger.info(
                "Skipping Webex room %s: human memberships below threshold",
                room_id,
            )
            continue

        messages = await wx.list_room_messages(room_id)
        grouped = group_webex_messages_by_thread(messages)

        for root in grouped.get(None, []):
            rid = str(root.get("id") or "")
            if not rid:
                continue
            replies = grouped.get(rid, [])
            built = build_webex_thread_chunk(
                root=root,
                replies=replies,
                org_id=org_id,
                room_id=room_id,
                room_title=title,
                bot_person_id=wx.bot_person_id,
                web_base=wx._web_base,
                skip_no_replies=settings.CHAT_INGEST_SKIP_NO_REPLIES,
            )
            if not built:
                continue
            chunk_dict, meta_json = built
            vectors = await embed_chunks([chunk_dict], settings.TEI_URL)
            await upsert_to_qdrant([chunk_dict], vectors, qdrant, collection_name)
            await write_chat_thread_to_postgres(
                source_type="webex",
                chunk=chunk_dict,
                chunk_metadata=meta_json,
                session=session,
            )
            written += 1

    return written

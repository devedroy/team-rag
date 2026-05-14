"""Signal filters for Teams/Webex chat ingestion."""

from __future__ import annotations

from typing import Any, Iterable

from teamrag.ingest.chat_thread import (
    teams_message_is_from_bot_or_app,
    webex_message_is_from_bot,
)


def distinct_human_author_ids_teams(messages: Iterable[dict[str, Any]]) -> set[str]:
    """Unique Graph user ids from messages, excluding apps/bots."""
    ids: set[str] = set()
    for m in messages:
        if teams_message_is_from_bot_or_app(m):
            continue
        user = (m.get("from") or {}).get("user") or {}
        uid = user.get("id")
        if uid:
            ids.add(str(uid))
    return ids


def distinct_human_person_ids_webex(
    messages: Iterable[dict[str, Any]],
    bot_person_id: str,
) -> set[str]:
    """Unique Webex personIds excluding the bot."""
    ids: set[str] = set()
    for m in messages:
        if webex_message_is_from_bot(m, bot_person_id):
            continue
        pid = (m.get("personId") or "").strip()
        if pid:
            ids.add(pid)
    return ids


def channel_meets_min_participants(count: int, minimum: int) -> bool:
    return count >= minimum


def skip_thread_no_replies(reply_count: int, skip_no_replies: bool) -> bool:
    """True if this thread should be skipped due to zero replies."""
    return skip_no_replies and reply_count == 0


def root_is_bot_teams(root: dict[str, Any]) -> bool:
    return teams_message_is_from_bot_or_app(root)


def root_is_bot_webex(root: dict[str, Any], bot_person_id: str) -> bool:
    return webex_message_is_from_bot(root, bot_person_id)

"""Shared chat-thread chunk schema, text normalization, and stable Qdrant IDs."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field
from markdownify import markdownify

ChatSource = Literal["teams", "webex"]


class ChatThreadChunkMetadata(BaseModel):
    """Metadata persisted to Postgres ``chunks.metadata`` and Qdrant payload."""

    source: ChatSource
    tenant_id: str = Field(description="Teams tenant id")
    org_id: str = Field(default="", description="Webex org id (empty for Teams)")
    channel_id: str = Field(default="", description="Teams channel id")
    space_id: str = Field(default="", description="Webex room id")
    channel_name: str = Field(default="", description="Teams channel display name")
    space_title: str = Field(default="", description="Webex room title")
    thread_id: str
    participants: list[str] = Field(default_factory=list)
    reply_count: int = 0
    reaction_count: int = 0
    has_code_block: bool = False
    last_activity_at: datetime
    source_url: str

    def to_chunk_metadata_dict(self) -> dict[str, Any]:
        """JSON-serializable dict for Postgres JSONB and Qdrant payload."""
        d = self.model_dump(mode="json")
        # Pydantic serializes datetime to ISO8601 in mode=json
        return d

    @property
    def page_title(self) -> str:
        """Human-visible title for ``ChunkResult.page_title`` / Qdrant ``page_title``."""
        return self.channel_name or self.space_title or "Chat"


_CODE_FENCE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_HTML_PRE = re.compile(r"<pre\b[^>]*>[\s\S]*?</pre>", re.IGNORECASE)


def deterministic_chat_chunk_id(
    source: ChatSource,
    tenant_or_org: str,
    channel_or_space_id: str,
    thread_id: str,
) -> str:
    """SHA256 hex id for one thread; first 16 hex chars become the Qdrant int id."""
    key = f"{source}:{tenant_or_org}:{channel_or_space_id}:{thread_id}"
    return hashlib.sha256(key.encode()).hexdigest()


def teams_message_is_from_bot_or_app(message: dict[str, Any]) -> bool:
    """True if Graph chatMessage is from an application/bot identity."""
    sender = message.get("from") or {}
    if sender.get("application") is not None:
        return True
    user = sender.get("user") or {}
    if user.get("userIdentityType") == "bot":
        return True
    return False


def webex_message_is_from_bot(message: dict[str, Any], bot_person_id: str) -> bool:
    """True if Webex message is authored by the configured bot."""
    pid = (message.get("personId") or "").strip()
    return bool(bot_person_id and pid == bot_person_id)


def strip_teams_message_body_to_text(body: dict[str, Any] | None) -> tuple[str, bool]:
    """Return (plain_text, has_code_block_hint) from Graph message body."""
    if not body:
        return "", False
    content = body.get("content") or ""
    ctype = (body.get("contentType") or "html").lower()
    has_code = bool(_HTML_PRE.search(content)) or "```" in content or "<pre" in content.lower()
    if ctype == "text":
        text = html.unescape(content).strip()
        return text, has_code or bool(_CODE_FENCE.search(text))
    md = markdownify(content, heading_style="ATX").strip()
    plain = re.sub(r"\n{3,}", "\n\n", md).strip()
    return plain, has_code or bool(_CODE_FENCE.search(plain))


def strip_webex_message_to_text(message: dict[str, Any]) -> tuple[str, bool]:
    """Prefer markdown, then text; detect code fences."""
    raw = (message.get("markdown") or message.get("text") or "").strip()
    if not raw:
        return "", False
    has_code = bool(_CODE_FENCE.search(raw)) or raw.count("`") >= 2
    # Light cleanup: collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", raw).strip()
    return text, has_code


def build_thread_document(
    ordered_messages: list[dict[str, Any]],
    *,
    platform: ChatSource,
    author_label: Callable[[dict[str, Any]], str],
    body_extractor: Callable[[dict[str, Any]], tuple[str, bool]],
) -> tuple[str, bool, int, int]:
    """Concatenate messages chronologically; return (text, has_code, reply_count, reaction_count).

    *ordered_messages* must be oldest-first. Each item is a platform-specific message dict.
    ``reply_count`` counts messages after the root (i.e. number of replies).
    ``reaction_count`` sums reaction-like structures if present on messages.
    """
    parts: list[str] = []
    any_code = False
    total_reactions = 0
    for msg in ordered_messages:
        text, hc = body_extractor(msg)
        any_code = any_code or hc
        label = author_label(msg)
        if text:
            parts.append(f"{label}: {text}")
        else:
            parts.append(f"{label}:")
        total_reactions += _reaction_count_from_message(msg, platform)
    body = "\n\n".join(parts).strip()
    reply_count = max(0, len(ordered_messages) - 1)
    return body, any_code, reply_count, total_reactions


def _reaction_count_from_message(msg: dict[str, Any], platform: ChatSource) -> int:
    if platform == "teams":
        rx = msg.get("reactions") or []
        if isinstance(rx, list):
            return len(rx)
        return 0
    # Webex may expose reactions in extensions; count emoji hits in markdown minimally
    if platform == "webex":
        rx = msg.get("reactions") or []
        if isinstance(rx, list):
            return len(rx)
    return 0


def parse_graph_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def thread_last_activity(
    ordered_messages: list[dict[str, Any]],
    *,
    platform: ChatSource,
) -> datetime | None:
    """Latest activity timestamp across messages."""
    best: datetime | None = None
    for msg in ordered_messages:
        if platform == "teams":
            ts = parse_graph_datetime(msg.get("lastModifiedDateTime") or msg.get("createdDateTime"))
        else:
            ts = parse_graph_datetime(msg.get("created"))
        if ts is None:
            continue
        if best is None or ts > best:
            best = ts
    return best


def graph_sender_display_name(msg: dict[str, Any]) -> str:
    user = (msg.get("from") or {}).get("user") or {}
    disp = user.get("displayName") or user.get("id") or "unknown"
    return str(disp)


def webex_sender_display_name(msg: dict[str, Any]) -> str:
    return str(msg.get("personEmail") or msg.get("personId") or "unknown")

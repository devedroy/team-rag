"""Unit tests for Phase 6 chat thread normalization, IDs, and signal helpers."""

from __future__ import annotations

from teamrag.ingest.chat_signal import (
    channel_meets_min_participants,
    distinct_human_author_ids_teams,
    root_is_bot_teams,
    skip_thread_no_replies,
)
from teamrag.ingest.chat_thread import (
    build_thread_document,
    deterministic_chat_chunk_id,
    graph_sender_display_name,
    strip_teams_message_body_to_text,
    strip_webex_message_to_text,
    teams_message_is_from_bot_or_app,
)
from teamrag.ingest.teams import build_teams_thread_chunk
from teamrag.ingest.webex import build_webex_thread_chunk


def test_deterministic_chat_chunk_id_stable() -> None:
    a = deterministic_chat_chunk_id("teams", "t1", "ch1", "th1")
    b = deterministic_chat_chunk_id("teams", "t1", "ch1", "th1")
    c = deterministic_chat_chunk_id("webex", "t1", "ch1", "th1")
    assert a == b
    assert len(a) == 64
    assert a != c


def test_strip_teams_html_to_text_and_code_hint() -> None:
    html = "<p>Hello <strong>world</strong></p><pre>code</pre>"
    text, has_code = strip_teams_message_body_to_text({"content": html, "contentType": "html"})
    assert "Hello" in text and "world" in text
    assert has_code is True


def test_strip_webex_markdown_code_fence() -> None:
    md = "Intro\n\n```python\nx = 1\n```\nOutro"
    text, has_code = strip_webex_message_to_text({"markdown": md})
    assert "x = 1" in text
    assert has_code is True


def test_build_thread_document_teams_order() -> None:
    def body(m: dict) -> tuple[str, bool]:
        return m["text"], False

    msgs = [
        {"text": "first", "from": {"user": {"displayName": "A"}}},
        {"text": "second", "from": {"user": {"displayName": "B"}}},
    ]
    doc, has_code, reply_count, _rx = build_thread_document(
        msgs,
        platform="teams",
        author_label=graph_sender_display_name,
        body_extractor=body,
    )
    assert "A:" in doc and "first" in doc
    assert "B:" in doc and "second" in doc
    assert doc.index("A:") < doc.index("B:")
    assert reply_count == 1
    assert has_code is False


def test_teams_bot_detection() -> None:
    app_msg = {"from": {"application": {"displayName": "Bot"}}}
    assert teams_message_is_from_bot_or_app(app_msg) is True
    human = {"from": {"user": {"displayName": "Pat", "id": "u1"}}}
    assert teams_message_is_from_bot_or_app(human) is False


def test_distinct_human_author_ids_teams() -> None:
    roots = [
        {"from": {"user": {"id": "1", "displayName": "A"}}},
        {"from": {"user": {"id": "2", "displayName": "B"}}},
        {"from": {"application": {"id": "bot"}}},
    ]
    assert distinct_human_author_ids_teams(roots) == {"1", "2"}


def test_channel_meets_min_and_skip_no_replies() -> None:
    assert channel_meets_min_participants(5, 5) is True
    assert channel_meets_min_participants(4, 5) is False
    assert skip_thread_no_replies(0, True) is True
    assert skip_thread_no_replies(1, True) is False


def test_build_teams_thread_chunk_skips_bot_root() -> None:
    root = {"from": {"application": {"id": "b1"}}, "id": "r1", "webUrl": "https://teams.test/x"}
    assert build_teams_thread_chunk(
        root=root,
        replies=[],
        tenant_id="tenant",
        channel_id="ch",
        channel_display_name="General",
        skip_no_replies=True,
    ) is None


def test_build_teams_thread_fixture() -> None:
    root = {
        "id": "root-1",
        "webUrl": "https://teams.microsoft.com/l/message/root-1",
        "createdDateTime": "2024-01-01T10:00:00Z",
        "lastModifiedDateTime": "2024-01-01T11:00:00Z",
        "from": {"user": {"id": "u1", "displayName": "Alice"}},
        "body": {"contentType": "html", "content": "<p>Root text</p>"},
        "reactions": [{"reactionType": "like"}],
    }
    replies = [
        {
            "id": "rep-1",
            "createdDateTime": "2024-01-01T10:30:00Z",
            "from": {"user": {"id": "u2", "displayName": "Bob"}},
            "body": {"contentType": "html", "content": "<p>Reply</p>"},
            "reactions": [],
        }
    ]
    out = build_teams_thread_chunk(
        root=root,
        replies=replies,
        tenant_id="tenant-x",
        channel_id="chan-y",
        channel_display_name="Engineering",
        skip_no_replies=True,
    )
    assert out is not None
    chunk, meta = out
    assert chunk["page_title"] == "Engineering"
    assert chunk["source"] == "teams"
    assert meta["source"] == "teams"
    assert meta["thread_id"] == "root-1"
    assert meta["reply_count"] == 1
    assert meta["reaction_count"] >= 1
    assert "Alice" in meta["participants"] and "Bob" in meta["participants"]
    cid = deterministic_chat_chunk_id("teams", "tenant-x", "chan-y", "root-1")
    assert chunk["chunk_id"] == cid


def test_build_webex_thread_fixture() -> None:
    root = {
        "id": "root-w",
        "roomId": "room1",
        "created": "2024-02-01T12:00:00.000Z",
        "personId": "p1",
        "personEmail": "alice@example.com",
        "markdown": "Topic start",
    }
    replies = [
        {
            "id": "r1",
            "parentId": "root-w",
            "created": "2024-02-01T12:05:00.000Z",
            "personId": "p2",
            "personEmail": "bob@example.com",
            "text": "Agreed",
        }
    ]
    out = build_webex_thread_chunk(
        root=root,
        replies=replies,
        org_id="org-o",
        room_id="room1",
        room_title="Webex Space",
        bot_person_id="bot-999",
        web_base="https://app.webex.com",
        skip_no_replies=True,
    )
    assert out is not None
    chunk, meta = out
    assert chunk["page_title"] == "Webex Space"
    assert meta["source"] == "webex"
    assert "app.webex.com" in chunk["source_url"]

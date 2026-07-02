"""Unit tests for Jira issue assembly and chunking (Phase 8)."""

from __future__ import annotations

import hashlib

from teamrag.ingest.jira import assemble_issue_document, chunk_issue_document

JIRA_URL = "https://example.atlassian.net"


def _adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]},
    ]}


def _issue(resolved: bool = True) -> dict:
    return {
        "key": "ENG-42",
        "fields": {
            "summary": "Rate limiting at the gateway",
            "description": _adf(
                "Tried token bucket at the gateway. See "
                "https://github.com/org/repo/pull/42"
            ),
            "status": {"name": "Done"},
            "assignee": {"displayName": "Alice"},
            "reporter": {"displayName": "Bob"},
            "labels": ["infra", "gateway"],
            "resolution": {"name": "Fixed"} if resolved else None,
            "updated": "2026-07-01T10:00:00.000+0000",
            "parent": {"key": "ENG-10"},
        },
    }


def _comments() -> list[dict]:
    return [
        {"author": {"displayName": "Carol"},
         "body": _adf("Worked well in staging.")},
        {"author": {"displayName": "Dave"}, "body": _adf("")},
    ]


def test_assemble_includes_title_description_comments_resolution():
    doc = assemble_issue_document(_issue(), _comments())
    assert "[ENG-42] Rate limiting at the gateway" in doc
    assert "Tried token bucket" in doc
    assert "**Carol**: Worked well in staging." in doc
    assert doc.rstrip().endswith("Resolution: Fixed")


def test_assemble_unresolved_line():
    doc = assemble_issue_document(_issue(resolved=False), [])
    assert doc.rstrip().endswith("Resolution: Unresolved")


def test_chunk_single_chunk_with_metadata_and_citation():
    issue = _issue()
    doc = assemble_issue_document(issue, _comments())
    chunks = chunk_issue_document(issue, doc, _comments(), JIRA_URL)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["source_url"] == f"{JIRA_URL}/browse/ENG-42"
    assert c["page_title"] == "[ENG-42] Rate limiting at the gateway"
    assert c["chunk_index"] == 0
    assert c["issue_key"] == "ENG-42"
    assert c["project_key"] == "ENG"
    assert c["status"] == "Done"
    assert c["assignee"] == "Alice"
    assert c["reporter"] == "Bob"
    assert c["labels"] == ["infra", "gateway"]
    assert c["epic"] == "ENG-10"
    assert c["resolution"] == "Fixed"
    assert c["linked_prs"] == ["https://github.com/org/repo/pull/42"]
    assert c["last_updated"] == "2026-07-01T10:00:00.000+0000"


def test_chunk_stable_id():
    issue = _issue()
    doc = assemble_issue_document(issue, [])
    c1 = chunk_issue_document(issue, doc, [], JIRA_URL)[0]
    c2 = chunk_issue_document(issue, doc, [], JIRA_URL)[0]
    assert c1["chunk_id"] == c2["chunk_id"]
    assert c1["chunk_id"] == hashlib.sha256(b"jira:ENG-42:0").hexdigest()


def test_chunk_empty_document_returns_no_chunks():
    issue = {"key": "ENG-9", "fields": {"summary": "", "description": None}}
    assert chunk_issue_document(issue, "", [], JIRA_URL) == []

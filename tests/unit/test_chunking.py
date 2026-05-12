"""Unit tests for the chunking pipeline — no external services needed."""

import pytest

from teamrag.ingest.pipeline import html_to_markdown, chunk_document

SAMPLE_PAGE = {
    "id": "123456",
    "title": "Auth Flow Overview",
    "space": {"key": "ENG"},
    "body": {
        "storage": {
            "value": """
            <h1>Auth Flow Overview</h1>
            <p>This page describes our authentication flow.</p>
            <h2>OAuth2 Setup</h2>
            <p>We use OAuth2 with PKCE for browser clients.</p>
            <h2>Token Refresh</h2>
            <p>Tokens expire after 1 hour and are refreshed silently via the refresh token.</p>
            """
        }
    },
    "version": {"when": "2024-01-15T10:00:00.000Z"},
    "_links": {"webui": "/wiki/spaces/ENG/pages/123456/Auth+Flow+Overview"},
}

CONFLUENCE_BASE_URL = "https://example.atlassian.net"


def test_html_to_markdown_returns_string():
    html = "<h1>Hello</h1><p>World</p>"
    result = html_to_markdown(html)
    assert isinstance(result, str)
    assert len(result) > 0


def test_html_to_markdown_preserves_headings():
    html = "<h1>Title</h1><h2>Subtitle</h2>"
    result = html_to_markdown(html)
    assert "Title" in result
    assert "Subtitle" in result


def test_chunk_document_returns_list():
    chunks = chunk_document(SAMPLE_PAGE, CONFLUENCE_BASE_URL)
    assert isinstance(chunks, list)
    assert len(chunks) >= 1


def test_chunk_document_required_fields():
    chunks = chunk_document(SAMPLE_PAGE, CONFLUENCE_BASE_URL)
    required_fields = {"content", "page_id", "page_title", "url", "space_key", "last_updated", "chunk_index"}
    for chunk in chunks:
        missing = required_fields - chunk.keys()
        assert not missing, f"Chunk missing fields: {missing}"


def test_chunk_document_content_nonempty():
    chunks = chunk_document(SAMPLE_PAGE, CONFLUENCE_BASE_URL)
    for chunk in chunks:
        assert chunk["content"].strip(), "Chunk content must not be empty"


def test_chunk_document_stable_ids():
    """Running chunk_document twice produces the same chunk IDs (idempotency)."""
    chunks_a = chunk_document(SAMPLE_PAGE, CONFLUENCE_BASE_URL)
    chunks_b = chunk_document(SAMPLE_PAGE, CONFLUENCE_BASE_URL)
    ids_a = [c["chunk_id"] for c in chunks_a]
    ids_b = [c["chunk_id"] for c in chunks_b]
    assert ids_a == ids_b


def test_chunk_document_url_format():
    chunks = chunk_document(SAMPLE_PAGE, CONFLUENCE_BASE_URL)
    for chunk in chunks:
        assert chunk["url"].startswith("https://"), f"URL must be absolute: {chunk['url']}"

"""Unit tests for GitHub PR assembler and chunker — no external services needed."""

import pytest

SAMPLE_PR = {
    "number": 42,
    "title": "Add payment flow",
    "body": "Implements the Stripe payment flow. Closes #10.",
    "user": {"login": "alice"},
    "merged_at": "2024-03-15T10:00:00Z",
    "base": {"repo": {"full_name": "org/backend"}},
}

SAMPLE_REVIEWS = [
    {"body": "LGTM. The error handling looks solid.", "state": "APPROVED"},
    {"body": "", "state": "COMMENTED"},  # empty body — must be skipped
]

SAMPLE_INLINE = [
    {"body": "Nit: use decimal.Decimal here for currency.", "path": "src/payment.py"},
]

SAMPLE_ISSUE_BODIES = ["Allow users to pay via credit card. Priority: high."]

REQUIRED_CHUNK_FIELDS = {
    "content", "pr_number", "pr_title", "author", "merged_at",
    "repo", "source_url", "chunk_index", "chunk_id",
}


def test_assemble_pr_document_includes_title_and_body():
    from teamrag.ingest.github import assemble_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    assert "Add payment flow" in doc
    assert "Implements the Stripe payment flow" in doc


def test_assemble_pr_document_includes_review_body():
    from teamrag.ingest.github import assemble_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    assert "LGTM" in doc


def test_assemble_pr_document_skips_empty_review_body():
    from teamrag.ingest.github import assemble_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, [], [])
    # Only one review has content; the empty one must not add a blank section
    assert doc.count("## Reviews") == 1


def test_assemble_pr_document_includes_inline_comments():
    from teamrag.ingest.github import assemble_pr_document
    doc = assemble_pr_document(SAMPLE_PR, [], SAMPLE_INLINE, [])
    assert "decimal.Decimal" in doc


def test_assemble_pr_document_includes_linked_issue():
    from teamrag.ingest.github import assemble_pr_document
    doc = assemble_pr_document(SAMPLE_PR, [], [], SAMPLE_ISSUE_BODIES)
    assert "credit card" in doc


def test_chunk_pr_document_returns_chunks():
    from teamrag.ingest.github import assemble_pr_document, chunk_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    chunks = chunk_pr_document(SAMPLE_PR, doc)
    assert len(chunks) >= 1


def test_chunk_pr_document_required_fields():
    from teamrag.ingest.github import assemble_pr_document, chunk_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    chunks = chunk_pr_document(SAMPLE_PR, doc)
    for chunk in chunks:
        missing = REQUIRED_CHUNK_FIELDS - chunk.keys()
        assert not missing, f"Chunk missing fields: {missing}"


def test_chunk_pr_document_source_url_format():
    from teamrag.ingest.github import assemble_pr_document, chunk_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    chunks = chunk_pr_document(SAMPLE_PR, doc)
    for chunk in chunks:
        assert chunk["source_url"] == "https://github.com/org/backend/pull/42"


def test_chunk_pr_document_stable_ids():
    """Running chunk_pr_document twice produces identical chunk IDs."""
    from teamrag.ingest.github import assemble_pr_document, chunk_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    ids_a = [c["chunk_id"] for c in chunk_pr_document(SAMPLE_PR, doc)]
    ids_b = [c["chunk_id"] for c in chunk_pr_document(SAMPLE_PR, doc)]
    assert ids_a == ids_b


def test_chunk_pr_document_content_nonempty():
    from teamrag.ingest.github import assemble_pr_document, chunk_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    chunks = chunk_pr_document(SAMPLE_PR, doc)
    for chunk in chunks:
        assert chunk["content"].strip(), "Chunk content must not be empty"


def test_chunk_has_url_and_page_title_compat_fields():
    """GitHub chunks must carry url + page_title so upsert_to_qdrant works unchanged."""
    from teamrag.acl import TIER_0_TAG
    from teamrag.ingest.github import assemble_pr_document, chunk_pr_document
    doc = assemble_pr_document(SAMPLE_PR, SAMPLE_REVIEWS, SAMPLE_INLINE, SAMPLE_ISSUE_BODIES)
    chunks = chunk_pr_document(SAMPLE_PR, doc)
    for chunk in chunks:
        assert "url" in chunk, "chunk must have 'url' for upsert_to_qdrant"
        assert "page_title" in chunk, "chunk must have 'page_title' for upsert_to_qdrant"
        assert chunk["url"] == chunk["source_url"]
        assert chunk["page_title"] == chunk["pr_title"]
        assert chunk.get("acl_tags") == [TIER_0_TAG]

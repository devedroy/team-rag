"""Unit tests for ADF text extraction and PR-link extraction (Phase 8)."""

from __future__ import annotations

from teamrag.ingest.jira import adf_to_text, extract_pr_links


def _adf_paragraph(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_adf_to_text_simple_paragraphs():
    adf = {"type": "doc", "version": 1, "content": [
        _adf_paragraph("First line."),
        _adf_paragraph("Second line."),
    ]}
    assert adf_to_text(adf) == "First line.\nSecond line."


def test_adf_to_text_nested_lists_and_marks():
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [_adf_paragraph("alpha")]},
            {"type": "listItem", "content": [_adf_paragraph("beta")]},
        ]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
            {"type": "hardBreak"},
            {"type": "text", "text": "after break"},
        ]},
    ]}
    text = adf_to_text(adf)
    assert "alpha" in text and "beta" in text
    assert "bold" in text and "after break" in text
    assert "\n" in text


def test_adf_to_text_handles_none_and_garbage():
    assert adf_to_text(None) == ""
    assert adf_to_text({}) == ""
    assert adf_to_text({"type": "doc", "content": [{"type": "weirdFutureNode"}]}) == ""


def test_extract_pr_links_unique_ordered():
    text = (
        "Fixed in https://github.com/org/repo/pull/42 see also "
        "https://github.com/org/other/pull/7 and again "
        "https://github.com/org/repo/pull/42 (dup). Not a PR: "
        "https://github.com/org/repo/issues/9"
    )
    assert extract_pr_links(text) == [
        "https://github.com/org/repo/pull/42",
        "https://github.com/org/other/pull/7",
    ]


def test_extract_pr_links_empty():
    assert extract_pr_links("") == []
    assert extract_pr_links("no links here") == []

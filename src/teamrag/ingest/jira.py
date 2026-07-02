"""Jira Cloud connector — Phase 8 ticket ingest.

One chunk per ticket: title + description + comments + resolution.
Jira Cloud REST v3 returns bodies as ADF (Atlassian Document Format)
JSON; ``adf_to_text`` extracts plain text. Citations use the
``/browse/{issue_key}`` URL.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def adf_to_text(adf: dict | None) -> str:
    """Extract plain text from an ADF document; never raises on odd shapes."""
    if not isinstance(adf, dict):
        return ""

    def _walk(node: dict) -> str:
        node_type = node.get("type")
        if node_type == "text":
            return str(node.get("text", ""))
        if node_type == "hardBreak":
            return "\n"
        children = node.get("content")
        if not isinstance(children, list):
            return ""
        parts = [_walk(child) for child in children if isinstance(child, dict)]
        joined = "".join(parts)
        # Block-level nodes end their text with a newline separator.
        if node_type in ("paragraph", "heading", "listItem", "blockquote", "codeBlock"):
            return joined + "\n"
        return joined

    text = _walk(adf)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def extract_pr_links(text: str) -> list[str]:
    """Unique GitHub PR URLs in first-seen order (Phase 2 cross-link)."""
    seen: list[str] = []
    for match in _PR_URL_RE.findall(text or ""):
        if match not in seen:
            seen.append(match)
    return seen

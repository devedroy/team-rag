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


_SEARCH_FIELDS = "summary,description,status,assignee,reporter,labels,resolution,updated,parent"


class JiraClient:
    """Async Jira Cloud REST v3 client (basic auth: email + API token)."""

    def __init__(self, settings) -> None:
        self._base_url = settings.JIRA_URL.rstrip("/")
        self._auth = (settings.JIRA_EMAIL, settings.JIRA_API_TOKEN)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "JiraClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url, auth=self._auth, timeout=30.0
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def search_issues(self, project_keys: list[str], max_issues: int):
        """Yield raw issue dicts, newest-updated first, across pages."""
        jql = f"project in ({','.join(project_keys)}) ORDER BY updated DESC"
        params: dict = {"jql": jql, "maxResults": 100, "fields": _SEARCH_FIELDS}
        yielded = 0
        while True:
            response = await self._client.get("/rest/api/3/search/jql", params=params)
            response.raise_for_status()
            data = response.json()
            issues = data.get("issues", [])
            for issue in issues:
                if yielded >= max_issues:
                    return
                yield issue
                yielded += 1
            token = data.get("nextPageToken")
            if not token or data.get("isLast") or not issues:
                return
            params = {**params, "nextPageToken": token}

    async def fetch_comments(self, issue_key: str) -> list[dict]:
        """All comments for an issue (paginated by startAt/total)."""
        comments: list[dict] = []
        start_at = 0
        while True:
            response = await self._client.get(
                f"/rest/api/3/issue/{issue_key}/comment",
                params={"startAt": start_at, "maxResults": 50},
            )
            response.raise_for_status()
            data = response.json()
            page = data.get("comments", [])
            comments.extend(page)
            total = int(data.get("total") or len(comments))
            start_at += len(page)
            if start_at >= total or not page:
                return comments


def assemble_issue_document(issue: dict, comments: list[dict]) -> str:
    """Ticket → one markdown document: title, description, comments, resolution."""
    fields = issue.get("fields", {}) or {}
    key = issue.get("key", "")
    summary = str(fields.get("summary") or "")
    parts: list[str] = [f"# [{key}] {summary}".strip()]

    description = adf_to_text(fields.get("description"))
    if description:
        parts.append(description)

    comment_lines: list[str] = []
    for comment in comments:
        author = ((comment.get("author") or {}).get("displayName")) or "unknown"
        body = adf_to_text(comment.get("body"))
        if body:
            comment_lines.append(f"**{author}**: {body}")
    if comment_lines:
        parts.append("## Comments\n" + "\n".join(comment_lines))

    resolution = (fields.get("resolution") or {}).get("name") if fields.get("resolution") else None
    parts.append(f"Resolution: {resolution or 'Unresolved'}")
    return "\n\n".join(parts)


def chunk_issue_document(
    issue: dict, document: str, comments: list[dict], jira_url: str
) -> list[dict]:
    """One chunk per ticket with citation + metadata; [] for empty documents."""
    key = issue.get("key", "")
    fields = issue.get("fields", {}) or {}
    summary = str(fields.get("summary") or "")
    if not key or not document.strip() or not summary.strip():
        logger.warning("Issue %r produced empty document — skipping", key)
        return []

    chunk_id = hashlib.sha256(f"jira:{key}:0".encode()).hexdigest()
    return [{
        "chunk_id": chunk_id,
        "content": document,
        "source_url": f"{jira_url.rstrip('/')}/browse/{key}",
        "page_title": f"[{key}] {summary}",
        "last_updated": str(fields.get("updated") or ""),
        "chunk_index": 0,
        "issue_key": key,
        "project_key": key.split("-", 1)[0] if "-" in key else key,
        "status": ((fields.get("status") or {}).get("name")) or "",
        "assignee": ((fields.get("assignee") or {}).get("displayName")) or "",
        "reporter": ((fields.get("reporter") or {}).get("displayName")) or "",
        "labels": list(fields.get("labels") or []),
        "epic": ((fields.get("parent") or {}).get("key")) or "",
        "resolution": ((fields.get("resolution") or {}).get("name")) or "",
        "linked_prs": extract_pr_links(document),
    }]

# Phase 8 — Jira Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m teamrag.ingest jira [--poll]` ingests Jira Cloud tickets (one chunk per ticket: title + description + comments + resolution) with status/assignee/labels/epic/linked-PR metadata and `/browse/` citations, ACL-mapped per project.

**Architecture:** New connector `src/teamrag/ingest/jira.py` mirrors the GitHub connector: async `JiraClient` (httpx, basic auth) + pure assembly/chunking functions; runner in `ingest/__main__.py` reuses the shared pipeline (`embed_chunks`, `upsert_to_qdrant`, ACL mapping, generic single-chunk Postgres writer). Jira v3 bodies are ADF JSON → pure `adf_to_text`. Polling re-index: `updated`-ordered JQL + idempotent upsert under stable chunk ids.

**Tech Stack:** httpx (present), Jira Cloud REST API v3 (`/rest/api/3/search/jql` with `nextPageToken` pagination — the classic `/rest/api/3/search` is deprecated/removed on Cloud), pytest fixtures (no live Jira in CI).

## Global Constraints

- All I/O async; config via Settings + `.env.example` updated alongside; no new dependencies.
- One chunk per ticket; `chunk_index` = 0; stable chunk id = sha256 of `"jira:{issue_key}:0"` (hex).
- Chunk dict keys the pipeline relies on: `chunk_id`, `content`, `source_url`, `page_title`, `last_updated`, `chunk_index`, plus metadata keys below. `source_url` = `{JIRA_URL rstrip /}/browse/{issue_key}`; `page_title` = `"[{issue_key}] {summary}"`.
- Metadata keys (also added to `OPTIONAL_QDRANT_PAYLOAD_KEYS`): `issue_key`, `project_key`, `status`, `assignee`, `reporter`, `labels`, `epic`, `resolution`, `linked_prs`.
- ACL: source type `"jira"`, resource key field `project_key`; Keycloak sync attribute `projects` → `jira`.
- New env vars: `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEYS`, `JIRA_MAX_ISSUES` (200), `JIRA_POLL_INTERVAL_SECONDS` (300).
- Never starlette TestClient; httpx AsyncClient + ASGITransport. TDD per task.
- Work on branch `phase-8-jira-ingest` off `main`.

---

### Task 1: Settings + ADF text extraction + PR-link extraction

**Files:**
- Modify: `src/teamrag/config.py`, `.env.example`
- Create: `src/teamrag/ingest/jira.py` (pure helpers only in this task)
- Test: `tests/unit/test_jira_adf.py`

**Interfaces:**
- Produces: settings `JIRA_URL: str = "https://your-org.atlassian.net"`, `JIRA_EMAIL: str = ""`, `JIRA_API_TOKEN: str = ""`, `JIRA_PROJECT_KEYS: str = ""`, `JIRA_MAX_ISSUES: int = 200`, `JIRA_POLL_INTERVAL_SECONDS: int = 300`.
- `def adf_to_text(adf: dict | None) -> str` — plain text from ADF; empty string for None/invalid; paragraphs/headings/list items newline-separated; text node `text` values concatenated in order; `hardBreak` → newline; unknown node types recursed through `content` (never crash).
- `def extract_pr_links(text: str) -> list[str]` — unique GitHub PR URLs (`https://github.com/<owner>/<repo>/pull/<n>`), first-seen order.

- [ ] **Step 1: Write failing tests** `tests/unit/test_jira_adf.py`

```python
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
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_jira_adf.py -q` — Expected: FAIL `ModuleNotFoundError: teamrag.ingest.jira` (jira module doesn't exist).

- [ ] **Step 3: Implement**

Append to `Settings` in `src/teamrag/config.py` (after the GitHub block, before Teams):

```python
    # Jira Cloud connector — Phase 8 ticket ingest
    JIRA_URL: str = "https://your-org.atlassian.net"
    JIRA_EMAIL: str = ""
    JIRA_API_TOKEN: str = ""
    JIRA_PROJECT_KEYS: str = ""       # comma-separated, e.g. "ENG,PAY"
    JIRA_MAX_ISSUES: int = 200
    JIRA_POLL_INTERVAL_SECONDS: int = 300
```

Add the same six vars to `.env.example` with placeholder values and a `# Jira Cloud connector (Phase 8)` comment.

Create `src/teamrag/ingest/jira.py`:

```python
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
```

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_jira_adf.py -q` — Expected: 5 passed. Then config smoke: `uv run python -c "from teamrag.config import settings; print(settings.JIRA_MAX_ISSUES)"` → `200`.

- [ ] **Step 5: Commit**

```bash
git add src/teamrag/config.py .env.example src/teamrag/ingest/jira.py tests/unit/test_jira_adf.py
git commit -m "feat(jira): add settings, ADF text extraction, PR-link extraction"
```

---

### Task 2: JiraClient (search + comments, paginated, mocked tests)

**Files:**
- Modify: `src/teamrag/ingest/jira.py`
- Test: `tests/unit/test_jira_client.py`

**Interfaces:**
- Produces: `class JiraClient` — `def __init__(self, settings)`; async context manager owning one `httpx.AsyncClient` (base_url = `settings.JIRA_URL`, `auth=(settings.JIRA_EMAIL, settings.JIRA_API_TOKEN)`, timeout 30); `async def search_issues(self, project_keys: list[str], max_issues: int) -> AsyncIterator[dict]` — JQL `project in (KEY1,KEY2) ORDER BY updated DESC` via `GET /rest/api/3/search/jql` with `nextPageToken` pagination, `maxResults=100`, `fields=summary,description,status,assignee,reporter,labels,resolution,updated,parent`; yields raw issue dicts, stops at `max_issues` or when `isLast`/no token; `async def fetch_comments(self, issue_key: str) -> list[dict]` — `GET /rest/api/3/issue/{issue_key}/comment` paginated by `startAt`/`total`, returns raw comment dicts.

- [ ] **Step 1: Write failing tests** `tests/unit/test_jira_client.py`

```python
"""JiraClient pagination tests against a mocked httpx transport."""

from __future__ import annotations

import json

import httpx
import pytest

from teamrag.ingest.jira import JiraClient


class _FakeSettings:
    JIRA_URL = "https://example.atlassian.net"
    JIRA_EMAIL = "bot@example.com"
    JIRA_API_TOKEN = "tok"


def _issue(key: str) -> dict:
    return {"key": key, "fields": {"summary": f"Summary {key}"}}


def _client_with_handler(handler) -> JiraClient:
    client = JiraClient(_FakeSettings())
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=_FakeSettings.JIRA_URL,
    )
    return client


@pytest.mark.asyncio
async def test_search_issues_paginates_with_next_page_token():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/search/jql"
        params = dict(request.url.params)
        calls.append(params)
        if "nextPageToken" not in params:
            return httpx.Response(200, json={
                "issues": [_issue("ENG-1"), _issue("ENG-2")],
                "nextPageToken": "tok2",
            })
        return httpx.Response(200, json={"issues": [_issue("ENG-3")]})

    client = _client_with_handler(handler)
    got = [i["key"] async for i in client.search_issues(["ENG"], max_issues=10)]
    await client._client.aclose()
    assert got == ["ENG-1", "ENG-2", "ENG-3"]
    assert len(calls) == 2
    assert "project in (ENG)" in calls[0]["jql"]
    assert calls[1]["nextPageToken"] == "tok2"


@pytest.mark.asyncio
async def test_search_issues_respects_max_issues():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "issues": [_issue("ENG-1"), _issue("ENG-2"), _issue("ENG-3")],
            "nextPageToken": "more",
        })

    client = _client_with_handler(handler)
    got = [i["key"] async for i in client.search_issues(["ENG"], max_issues=2)]
    await client._client.aclose()
    assert got == ["ENG-1", "ENG-2"]


@pytest.mark.asyncio
async def test_fetch_comments_paginates_start_at():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/ENG-1/comment"
        start_at = int(dict(request.url.params).get("startAt", 0))
        if start_at == 0:
            return httpx.Response(200, json={
                "comments": [{"id": "1"}, {"id": "2"}], "startAt": 0,
                "maxResults": 2, "total": 3,
            })
        return httpx.Response(200, json={
            "comments": [{"id": "3"}], "startAt": 2, "maxResults": 2, "total": 3,
        })

    client = _client_with_handler(handler)
    comments = await client.fetch_comments("ENG-1")
    await client._client.aclose()
    assert [c["id"] for c in comments] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_search_issues_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorMessages": ["bad auth"]})

    client = _client_with_handler(handler)
    with pytest.raises(httpx.HTTPStatusError):
        _ = [i async for i in client.search_issues(["ENG"], max_issues=5)]
    await client._client.aclose()
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_jira_client.py -q` — Expected: FAIL `ImportError: cannot import name 'JiraClient'`.

- [ ] **Step 3: Implement** — append to `src/teamrag/ingest/jira.py`:

```python
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
            for issue in data.get("issues", []):
                yield issue
                yielded += 1
                if yielded >= max_issues:
                    return
            token = data.get("nextPageToken")
            if not token or data.get("isLast"):
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
            total = int(data.get("total", len(comments)))
            start_at += len(page)
            if start_at >= total or not page:
                return comments
```

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_jira_client.py tests/unit/test_jira_adf.py -q` — Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/teamrag/ingest/jira.py tests/unit/test_jira_client.py
git commit -m "feat(jira): async Jira Cloud client with paginated search and comments"
```

---

### Task 3: Assembly + chunking + payload keys + ACL/sync extensions

**Files:**
- Modify: `src/teamrag/ingest/jira.py`, `src/teamrag/ingest/pipeline.py` (`OPTIONAL_QDRANT_PAYLOAD_KEYS`), `src/teamrag/ingest/acl_mapping.py` (`_RESOURCE_KEY_FIELDS`), `src/teamrag/sync/__init__.py` (`_ATTRIBUTE_SOURCE_TYPES`)
- Test: `tests/unit/test_jira_chunking.py`; extend `tests/unit/test_acl_mapping.py` and `tests/unit/test_sync.py`

**Interfaces:**
- Produces:
  - `def assemble_issue_document(issue: dict, comments: list[dict]) -> str` — sections: `# [{key}] {summary}`, description text, `## Comments` (each `**{author}**: {text}`, ADF-extracted, empty comments skipped), final line `Resolution: {resolution name}` or `Resolution: Unresolved`.
  - `def chunk_issue_document(issue: dict, document: str, comments: list[dict], jira_url: str) -> list[dict]` — `[]` if document effectively empty, else exactly one chunk dict with Global Constraints keys. `status` = `fields.status.name` or `""`; `assignee`/`reporter` = `.displayName` or `""`; `labels` = list; `epic` = `fields.parent.key` or `""`; `resolution` = `fields.resolution.name` or `""`; `linked_prs` = `extract_pr_links(document)`; `last_updated` = `fields.updated` or `""`; `acl_tags` omitted (tier-0 default via `merge_acl_tags_for_ingest`; overridden by `apply_acl_mapping`).
  - Pipeline: 9 new payload keys appended to `OPTIONAL_QDRANT_PAYLOAD_KEYS`.
  - ACL: `"jira": "project_key"` in `_RESOURCE_KEY_FIELDS`; sync: `"projects": "jira"` in `_ATTRIBUTE_SOURCE_TYPES`.

- [ ] **Step 1: Write failing tests** `tests/unit/test_jira_chunking.py`

```python
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
```

Extend `tests/unit/test_acl_mapping.py`:

```python
def test_resource_key_for_chunk_jira_project():
    assert resource_key_for_chunk("jira", {"project_key": "PAY"}) == "PAY"
```

Extend `tests/unit/test_sync.py` (inside the existing attribute-mapping test or as a new one):

```python
def test_mappings_from_groups_maps_projects_to_jira():
    groups = [{"name": "squad-payments", "attributes": {"projects": ["PAY"]}}]
    assert ("jira", "PAY", ["squad-payments", "tier-1"]) in mappings_from_groups(groups)
```

- [ ] **Step 2: Run** the three test files — Expected: new tests FAIL (missing functions/keys).

- [ ] **Step 3: Implement** — append to `src/teamrag/ingest/jira.py`:

```python
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
```

In `src/teamrag/ingest/pipeline.py`, extend `OPTIONAL_QDRANT_PAYLOAD_KEYS` (append inside the tuple, with a `# Phase 8 Jira` comment line):

```python
    # Phase 8 Jira
    "issue_key",
    "project_key",
    "status",
    "assignee",
    "reporter",
    "labels",
    "epic",
    "resolution",
    "linked_prs",
```

In `src/teamrag/ingest/acl_mapping.py`, add to `_RESOURCE_KEY_FIELDS`: `"jira": "project_key",`.
In `src/teamrag/sync/__init__.py`, add to `_ATTRIBUTE_SOURCE_TYPES`: `"projects": "jira",`.

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_jira_chunking.py tests/unit/test_acl_mapping.py tests/unit/test_sync.py -q` — Expected: all pass. Then full unit suite once: `uv run pytest tests/unit -q`.

- [ ] **Step 5: Commit**

```bash
git add src/teamrag/ingest/jira.py src/teamrag/ingest/pipeline.py src/teamrag/ingest/acl_mapping.py src/teamrag/sync/__init__.py tests/unit/test_jira_chunking.py tests/unit/test_acl_mapping.py tests/unit/test_sync.py
git commit -m "feat(jira): one-chunk-per-ticket assembly with metadata, ACL and sync wiring"
```

---

### Task 4: CLI runner `python -m teamrag.ingest jira [--poll]`

**Files:**
- Modify: `src/teamrag/ingest/__main__.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3; existing pipeline helpers (`_ensure_qdrant_collection`, `embed_chunks`, `upsert_to_qdrant`, `write_chat_thread_to_postgres`, `apply_acl_mapping`, `load_acl_mappings`); existing `main()` dispatch and `--poll` convention (mirror how teams/webex runners handle poll loops — read the file and follow it).
- Produces: `_run_jira()` coroutine + `jira` wired into `main()`'s source dispatch (including `--poll` support via `JIRA_POLL_INTERVAL_SECONDS`).

- [ ] **Step 1: Implement `_run_jira`** in `src/teamrag/ingest/__main__.py`, modeled on `_run_github` (env validation → clients → per-issue: comments → assemble → chunk → ACL → embed → upsert → Postgres):

```python
async def _run_jira() -> None:
    from qdrant_client import AsyncQdrantClient

    from teamrag.config import settings
    from teamrag.db.session import get_session

    missing = [
        var for var, val in [
            ("JIRA_URL", settings.JIRA_URL),
            ("JIRA_EMAIL", settings.JIRA_EMAIL),
            ("JIRA_API_TOKEN", settings.JIRA_API_TOKEN),
            ("JIRA_PROJECT_KEYS", settings.JIRA_PROJECT_KEYS),
        ]
        if not val or val in ("https://your-org.atlassian.net",)
    ]
    if missing:
        logger.error(
            "Missing or unconfigured Jira credentials: %s. Set these in .env and retry.",
            ", ".join(missing),
        )
        sys.exit(1)

    from teamrag.ingest.acl_mapping import apply_acl_mapping, load_acl_mappings
    from teamrag.ingest.jira import JiraClient, assemble_issue_document, chunk_issue_document
    from teamrag.ingest.pipeline import (
        embed_chunks,
        upsert_to_qdrant,
        write_chat_thread_to_postgres,
    )

    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)
    try:
        await _ensure_qdrant_collection(qdrant, settings)

        project_keys = [k.strip() for k in settings.JIRA_PROJECT_KEYS.split(",") if k.strip()]
        issues_processed = 0

        async with JiraClient(settings) as jira:
            async for session in get_session():
                mappings = await load_acl_mappings(session)
                async for issue in jira.search_issues(project_keys, settings.JIRA_MAX_ISSUES):
                    issue_key = issue.get("key", "")
                    comments = await jira.fetch_comments(issue_key)
                    document = assemble_issue_document(issue, comments)
                    chunks = chunk_issue_document(issue, document, comments, settings.JIRA_URL)
                    if not chunks:
                        continue
                    for chunk in chunks:
                        apply_acl_mapping(chunk, "jira", mappings)

                    vectors = await embed_chunks(chunks, settings.TEI_URL)
                    await upsert_to_qdrant(chunks, vectors, qdrant, settings.QDRANT_COLLECTION)
                    chunk = chunks[0]
                    await write_chat_thread_to_postgres(
                        source_type="jira",
                        chunk=chunk,
                        chunk_metadata={
                            "issue_key": chunk["issue_key"],
                            "project_key": chunk["project_key"],
                            "status": chunk["status"],
                            "resolution": chunk["resolution"],
                            "labels": chunk["labels"],
                            "epic": chunk["epic"],
                            "linked_prs": chunk["linked_prs"],
                        },
                        session=session,
                    )
                    issues_processed += 1

        logger.info("Jira ingest complete: %d issues processed", issues_processed)
    finally:
        await qdrant.close()
```

- [ ] **Step 2: Wire dispatch.** Read `main()` and the module docstring; add `jira` to the accepted sources (docstring: `<confluence|github|jira|teams|webex|chat>`). Follow the exact existing pattern for single-run vs `--poll`: if the chat runners use a shared poll wrapper, reuse it with `settings.JIRA_POLL_INTERVAL_SECONDS`; otherwise implement the same `while True: run; sleep(interval)` shape the file already uses. Keep consistent with whatever `main()` does for teams/webex.

- [ ] **Step 3: Verify**

```bash
uv run python -c "import teamrag.ingest.__main__ as m; print('ok')"
uv run python -m teamrag.ingest 2>&1 | head -3   # usage line should now mention jira
uv run python -m teamrag.ingest jira 2>&1 | tail -2  # exits 1: missing Jira credentials
uv run pytest tests/unit -q
```
Expected: `ok`; usage mentions jira; credential error + exit 1; full unit suite passes.

- [ ] **Step 4: Commit**

```bash
git add src/teamrag/ingest/__main__.py
git commit -m "feat(jira): CLI runner with poll mode for ticket ingest"
```

---

### Task 5: Integration test + validation + docs

**Files:**
- Create: `tests/integration/test_phase8_jira.py`
- Create: `specs/2026-07-03-phase-8-jira-ingest/validation.md` (`git add -f`)
- Modify: `README.md` (Phase 8 section), `CLAUDE.md` (ingest sources list / project structure mentions of connectors)

**Interfaces:**
- Consumes: live Qdrant + TEI (skip gracefully if down); the chunk shape from Task 3.

- [ ] **Step 1: Write integration test** `tests/integration/test_phase8_jira.py` — Phase 6 pattern: build a resolved-ticket chunk via the real `assemble_issue_document`/`chunk_issue_document` (reuse the unit fixtures' issue shape with summary "Rate limiting at the gateway", resolution Fixed, a linked PR), embed via TEI (`embed_query_text`, skip if unreachable), upsert to Qdrant with the pipeline's `upsert_to_qdrant` (unique random suffix in content to avoid cross-run collisions is NOT needed — stable id is the point; clean up in finally with the computed point id `int(chunk_id[:16], 16)`), then:

```python
@pytest.mark.asyncio
async def test_query_returns_resolved_ticket_with_citation(jira_point):
    from teamrag.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            "/query",
            json={"query": "have we tried rate-limiting at the gateway before?", "top_k": 10},
        )
    assert response.status_code == 200
    urls = {c["source_url"] for c in response.json()["chunks"]}
    assert "https://example.atlassian.net/browse/ENG-42" in urls
```

plus a payload-shape test (scroll or query the point and assert `status == "Done"`, `resolution == "Fixed"`, `linked_prs` non-empty — mirroring how test_phase6 asserts payload metadata). Fixture skips if TEI/Qdrant unreachable, deletes the point in `finally`.

- [ ] **Step 2: Run the full suite against the live stack**

```bash
export DATABASE_URL=postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag
uv run pytest tests/ -q
```
Expected: previous profile (115 passed, 2 skipped) plus the new tests passing.

- [ ] **Step 3: Write `specs/2026-07-03-phase-8-jira-ingest/validation.md`** — commands + real output: unit suites, full suite, the done-when evidence (query returns the /browse/ citation with resolution metadata). Follow the Phase 7 validation.md structure.

- [ ] **Step 4: Docs** — README: Phase 8 section (env vars, `python -m teamrag.ingest jira [--poll]`, what gets indexed, ACL note: Keycloak group attribute `projects` gates Jira projects). CLAUDE.md: add jira to the connector list mentions (ingest module tree + ACL sync attribute list if present).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_phase8_jira.py README.md CLAUDE.md
git add -f specs/2026-07-03-phase-8-jira-ingest/validation.md
git commit -m "test(jira): Phase 8 end-to-end retrieval test + validation + docs"
```

---

## Self-review notes

- Roadmap coverage: one-chunk-per-ticket (T3), metadata incl. epic/linked_prs (T3), re-index on status change via poll+idempotent upsert (T4 + spec Decision 2), cross-link metadata (T3 linked_prs), done-when query (T5).
- Type consistency: chunk dict keys used in T4's Postgres metadata all produced in T3; `search_issues(project_keys: list[str], max_issues: int)` matches T4 call; `chunk_issue_document(issue, document, comments, jira_url)` signature consistent across T3/T5.
- No new deps; ADF handled with stdlib recursion.

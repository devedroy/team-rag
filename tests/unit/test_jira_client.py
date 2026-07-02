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

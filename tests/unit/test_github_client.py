"""Smoke-import test — no network, no credentials needed."""

def test_github_client_importable():
    from teamrag.ingest.github import GitHubClient
    assert GitHubClient is not None


def test_extract_issue_refs():
    from teamrag.ingest.github import GitHubClient

    class _FakeSettings:
        GITHUB_TOKEN = "x"
        GITHUB_MAX_PRS = 10

    client = GitHubClient(_FakeSettings())
    refs = client._extract_issue_refs("Fixes #42 and #100. See also #7.")
    assert refs == [42, 100, 7]


def test_extract_issue_refs_empty():
    from teamrag.ingest.github import GitHubClient

    class _FakeSettings:
        GITHUB_TOKEN = "x"
        GITHUB_MAX_PRS = 10

    client = GitHubClient(_FakeSettings())
    assert client._extract_issue_refs("No refs here.") == []

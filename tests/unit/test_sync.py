"""Unit tests for the Keycloak → resource_acl_mappings sync (pure parts)."""

from __future__ import annotations

import sqlalchemy as sa

from teamrag.sync import (
    aggregate_mapping_rows,
    keys_to_prune,
    mappings_from_groups,
    parse_seed_arg,
    prune_stale_mappings,
)


def test_mappings_from_groups_maps_attributes_to_source_types():
    groups = [
        {
            "name": "squad-payments",
            "attributes": {
                "repos": ["org/payments-svc", "org/billing"],
                "channels": ["19:abc"],
            },
        },
        {"name": "squad-platform", "attributes": {"rooms": ["room-1"], "spaces": ["PLAT"]}},
        {"name": "no-attrs", "attributes": {}},
    ]
    rows = mappings_from_groups(groups)
    assert ("github", "org/payments-svc", ["squad-payments", "tier-1"]) in rows
    assert ("github", "org/billing", ["squad-payments", "tier-1"]) in rows
    assert ("teams", "19:abc", ["squad-payments", "tier-1"]) in rows
    assert ("webex", "room-1", ["squad-platform", "tier-1"]) in rows
    assert ("confluence", "PLAT", ["squad-platform", "tier-1"]) in rows
    assert len(rows) == 5


def test_parse_seed_arg():
    assert parse_seed_arg("github:org/repo:squad-payments,tier-1") == (
        "github",
        "org/repo",
        ["squad-payments", "tier-1"],
    )


def test_parse_seed_arg_keeps_colons_in_resource_key():
    assert parse_seed_arg("teams:19:abc@thread.tacv2:squad-payments,tier-1") == (
        "teams",
        "19:abc@thread.tacv2",
        ["squad-payments", "tier-1"],
    )


def test_parse_seed_arg_rejects_malformed():
    import pytest

    with pytest.raises(ValueError):
        parse_seed_arg("just-nonsense")

    with pytest.raises(ValueError):
        parse_seed_arg("github:org/repo")  # missing tags part

    with pytest.raises(ValueError):
        parse_seed_arg("github::squad-payments")  # empty resource key


def test_aggregate_mapping_rows_merges_duplicate_resources():
    groups = [
        {"name": "squad-payments", "attributes": {"repos": ["org/shared-repo"]}},
        {"name": "squad-platform", "attributes": {"repos": ["org/shared-repo", "org/platform"]}},
    ]
    rows = aggregate_mapping_rows(mappings_from_groups(groups))
    assert rows == [
        ("github", "org/shared-repo", ["squad-payments", "tier-1", "squad-platform"]),
        ("github", "org/platform", ["squad-platform", "tier-1"]),
    ]


def test_aggregate_mapping_rows_dedupes_tags_and_preserves_order():
    rows = aggregate_mapping_rows(
        [
            ("teams", "19:abc", ["squad-a", "tier-1"]),
            ("teams", "19:abc", ["squad-a", "tier-1"]),
            ("teams", "19:abc", ["squad-b", "tier-1"]),
        ]
    )
    assert rows == [("teams", "19:abc", ["squad-a", "tier-1", "squad-b"])]


# --- pruning (revocation convergence) ---


def test_keys_to_prune_returns_stale_keys_only():
    existing = {("github", "org/a"), ("github", "org/stale"), ("teams", "19:x")}
    fetched = {("github", "org/a"), ("teams", "19:x")}
    assert keys_to_prune(existing, fetched) == {("github", "org/stale")}


def test_keys_to_prune_empty_when_nothing_stale():
    existing = {("github", "org/a")}
    fetched = {("github", "org/a"), ("github", "org/new")}
    assert keys_to_prune(existing, fetched) == set()


class _FakeSelectResult:
    def __init__(self, rows: list[tuple[str, str]]):
        self._rows = rows

    def all(self):
        return self._rows


class _StubPruneSession:
    """Records executed statements; answers SELECT with canned existing rows."""

    def __init__(self, existing_rows: list[tuple[str, str]]):
        self._existing_rows = existing_rows
        self.executed: list = []
        self.committed = False

    async def execute(self, stmt):
        self.executed.append(stmt)
        if isinstance(stmt, sa.sql.Select):
            return _FakeSelectResult(self._existing_rows)
        return None

    async def commit(self):
        self.committed = True

    @property
    def deletes(self):
        return [s for s in self.executed if isinstance(s, sa.sql.Delete)]


async def test_prune_stale_mappings_deletes_rows_absent_from_fetch():
    session = _StubPruneSession([("github", "org/a"), ("github", "org/stale")])
    fetched_rows = [("github", "org/a", ["squad-payments", "tier-1"])]

    pruned = await prune_stale_mappings(session, fetched_rows)

    assert pruned == 1
    assert len(session.deletes) == 1
    assert session.committed is True


async def test_prune_stale_mappings_issues_no_delete_when_nothing_stale():
    session = _StubPruneSession([("github", "org/a")])
    fetched_rows = [("github", "org/a", ["squad-payments", "tier-1"])]

    pruned = await prune_stale_mappings(session, fetched_rows)

    assert pruned == 0
    assert len(session.deletes) == 0


async def test_run_prunes_on_full_sync_but_not_on_seed(monkeypatch):
    """Full sync (no --seed) must prune; --seed must never prune."""
    import teamrag.sync.__main__ as sync_main

    prune_calls: list[list] = []

    async def _fake_upsert_mappings(session, rows):
        return len(rows)

    async def _fake_prune_stale_mappings(session, fetched_rows):
        prune_calls.append(fetched_rows)
        return 0

    async def _fake_fetch_keycloak_groups(**kwargs):
        return [{"name": "squad-payments", "attributes": {"repos": ["org/a"]}}]

    class _NoopSession:
        pass

    async def _fake_get_session():
        yield _NoopSession()

    monkeypatch.setattr("teamrag.sync.upsert_mappings", _fake_upsert_mappings)
    monkeypatch.setattr("teamrag.sync.prune_stale_mappings", _fake_prune_stale_mappings)
    monkeypatch.setattr("teamrag.sync.fetch_keycloak_groups", _fake_fetch_keycloak_groups)
    monkeypatch.setattr("teamrag.db.session.get_session", _fake_get_session)

    from teamrag.config import settings

    monkeypatch.setattr(settings, "KEYCLOAK_ADMIN_PASSWORD", "dummy")

    # Seed path: prune must NOT be called.
    await sync_main._run([("github", "org/seeded", ["squad-payments", "tier-1"])])
    assert prune_calls == []

    # Full-sync path: prune must be called with the fetched rows.
    await sync_main._run([])
    assert len(prune_calls) == 1

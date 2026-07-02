"""Unit tests for ingest-side resource → ACL tag mapping."""

from __future__ import annotations

from teamrag.ingest.acl_mapping import apply_acl_mapping, resource_key_for_chunk

MAPPINGS = {
    ("github", "org/payments-svc"): ["squad-payments", "tier-1"],
    ("teams", "channel-19:abc"): ["squad-payments", "tier-1"],
}


def test_resource_key_for_chunk_by_source_type():
    assert resource_key_for_chunk("github", {"repo": "org/payments-svc"}) == "org/payments-svc"
    assert resource_key_for_chunk("confluence", {"space_key": "ENG"}) == "ENG"
    assert resource_key_for_chunk("teams", {"channel_id": "channel-19:abc"}) == "channel-19:abc"
    assert resource_key_for_chunk("webex", {"space_id": "room-1"}) == "room-1"
    assert resource_key_for_chunk("github", {}) is None


def test_apply_acl_mapping_sets_tags_on_match():
    chunk = {"repo": "org/payments-svc", "acl_tags": ["tier-0"]}
    apply_acl_mapping(chunk, "github", MAPPINGS)
    assert chunk["acl_tags"] == ["squad-payments", "tier-1"]


def test_apply_acl_mapping_no_match_leaves_chunk_untouched():
    chunk = {"repo": "org/public-repo", "acl_tags": ["tier-0"]}
    apply_acl_mapping(chunk, "github", MAPPINGS)
    assert chunk["acl_tags"] == ["tier-0"]


def test_apply_acl_mapping_unknown_source_type_noop():
    chunk = {"acl_tags": ["tier-0"]}
    apply_acl_mapping(chunk, "jira", MAPPINGS)
    assert chunk["acl_tags"] == ["tier-0"]

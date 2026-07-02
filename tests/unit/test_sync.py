"""Unit tests for the Keycloak → resource_acl_mappings sync (pure parts)."""

from __future__ import annotations

from teamrag.sync import mappings_from_groups, parse_seed_arg


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


def test_parse_seed_arg_rejects_malformed():
    import pytest

    with pytest.raises(ValueError):
        parse_seed_arg("just-nonsense")

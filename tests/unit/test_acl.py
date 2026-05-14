"""Unit tests for Phase 5 ACL tag helpers."""

from __future__ import annotations

import pytest

from teamrag.acl import (
    AclFilterMode,
    TIER_0_TAG,
    merge_acl_tags_for_ingest,
    qdrant_filter_for_mode,
    qdrant_filter_scroll_by_source_url,
)


def test_merge_acl_tags_defaults_to_tier0() -> None:
    assert merge_acl_tags_for_ingest({}) == [TIER_0_TAG]


def test_merge_acl_tags_empty_list_defaults() -> None:
    assert merge_acl_tags_for_ingest({"acl_tags": []}) == [TIER_0_TAG]


def test_merge_acl_tags_preserves_explicit_tags() -> None:
    assert merge_acl_tags_for_ingest({"acl_tags": ["tier-0", "squad-x"]}) == [
        "tier-0",
        "squad-x",
    ]


def test_qdrant_filter_scroll_by_source_url_includes_acl() -> None:
    pytest.importorskip("qdrant_client")
    from qdrant_client.models import Filter, MatchAny

    flt = qdrant_filter_scroll_by_source_url(
        ["https://a.example/x", "https://a.example/x/"],
        AclFilterMode.UNAUTHENTICATED_TIER_0,
    )
    assert isinstance(flt, Filter)
    assert flt.must is not None and len(flt.must) == 2
    url_cond, nested_acl = flt.must[0], flt.must[1]
    assert isinstance(url_cond.match, MatchAny)
    assert isinstance(nested_acl, Filter)
    assert nested_acl.should is not None and len(nested_acl.should) == 2


def test_qdrant_filter_unauthenticated_tier0() -> None:
    pytest.importorskip("qdrant_client")
    from qdrant_client.models import Filter, IsEmptyCondition, MatchValue

    flt = qdrant_filter_for_mode(AclFilterMode.UNAUTHENTICATED_TIER_0)
    assert isinstance(flt, Filter)
    assert flt.should is not None
    assert len(flt.should) == 2
    tier0_cond, missing_cond = flt.should[0], flt.should[1]
    assert tier0_cond.key == "acl_tags"
    assert isinstance(tier0_cond.match, MatchValue)
    assert tier0_cond.match.value == TIER_0_TAG
    assert isinstance(missing_cond, IsEmptyCondition)
    assert missing_cond.is_empty.key == "acl_tags"
    assert flt.min_should is None

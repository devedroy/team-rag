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
    from qdrant_client.models import Filter, MatchAny, MatchValue

    flt = qdrant_filter_scroll_by_source_url(
        ["https://a.example/x", "https://a.example/x/"],
        AclFilterMode.UNAUTHENTICATED_TIER_0,
    )
    assert isinstance(flt, Filter)
    assert flt.must is not None and len(flt.must) == 2
    url_cond, acl_cond = flt.must[0], flt.must[1]
    assert isinstance(url_cond.match, MatchAny)
    assert isinstance(acl_cond.match, MatchValue)
    assert acl_cond.match.value == TIER_0_TAG


def test_qdrant_filter_unauthenticated_tier0() -> None:
    pytest.importorskip("qdrant_client")
    from qdrant_client.models import Filter, MatchValue

    flt = qdrant_filter_for_mode(AclFilterMode.UNAUTHENTICATED_TIER_0)
    assert isinstance(flt, Filter)
    assert flt.must is not None
    assert len(flt.must) == 1
    cond = flt.must[0]
    assert cond.key == "acl_tags"
    m = cond.match
    assert isinstance(m, MatchValue)
    assert m.value == TIER_0_TAG

"""Tier and ACL tag constants plus Qdrant filter helpers.

Phase 5 enforces **unauthenticated = tier-0 visibility** at retrieval time.
Authenticated callers with IdP-derived ``user_groups`` are deferred (roadmap
Phase 7+); all traffic today is treated as unauthenticated and filtered in
Qdrant to points whose ``acl_tags`` payload includes ``tier-0``.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Canonical public / engineering-wide tier tag — must match Postgres ``acl_tags.tag``
# and Qdrant payload field ``acl_tags`` string values.
TIER_0_TAG: str = "tier-0"


class AclFilterMode(str, enum.Enum):
    """How retrieval constrains Qdrant results."""

    UNAUTHENTICATED_TIER_0 = "unauthenticated_tier0"


def merge_acl_tags_for_ingest(chunk: dict[str, Any]) -> list[str]:
    """Return ACL tags to persist for an ingest chunk dict.

    If ``acl_tags`` is missing or empty, default to **tier-0** only.
    Otherwise return a shallow copy of the provided string tags.
    """
    raw = chunk.get("acl_tags")
    if not raw:
        return [TIER_0_TAG]
    tags = [str(t) for t in raw]
    return tags if tags else [TIER_0_TAG]


def qdrant_filter_for_mode(mode: AclFilterMode):
    """Build a Qdrant ``Filter`` for the given ACL mode (lazy qdrant imports).

    Missing ``acl_tags`` is treated as tier-0 for backward compatibility with
    points ingested before Phase 5 (consistent with merge_acl_tags_for_ingest
    defaulting to tier-0 when the field is absent).
    """
    from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

    if mode is AclFilterMode.UNAUTHENTICATED_TIER_0:
        return Filter(
            should=[
                FieldCondition(key="acl_tags", match=MatchValue(value=TIER_0_TAG)),
                IsEmptyCondition(is_empty=PayloadField(key="acl_tags")),
            ],
        )
    raise ValueError(f"Unsupported ACL filter mode: {mode!r}")


def qdrant_filter_scroll_by_source_url(source_url_variants: list[str], mode: AclFilterMode):
    """Qdrant scroll filter: ``source_url`` matches one of *variants* and ACL *mode* applies."""
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    tier = qdrant_filter_for_mode(mode)
    return Filter(
        must=[
            FieldCondition(
                key="source_url",
                match=MatchAny(any=source_url_variants),
            ),
            tier,
        ],
    )


def resolve_acl_filter_mode_from_request(_request: Any) -> AclFilterMode:
    """Pick Qdrant ACL filter mode from the incoming HTTP request.

    Phase 5: every caller is treated as unauthenticated; tier-0-only filtering
    always applies. Future phases may inspect JWT / headers here.
    """
    return AclFilterMode.UNAUTHENTICATED_TIER_0


def log_acl_filter_mode(mode: AclFilterMode) -> None:
    """Log non-sensitive ACL retrieval mode for operators."""
    logger.debug("Qdrant ACL filter mode: %s", mode.value)

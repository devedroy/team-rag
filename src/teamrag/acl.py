"""Tier and ACL tag constants plus Qdrant filter helpers.

Current retrieval-time ACL model (Phase 7):

- **Unauthenticated** callers (no ``Authorization`` header, or auth disabled
  via unset ``OIDC_ISSUER``) are filtered to tier-0 only — the tier-0/public
  visibility introduced in Phase 5.
- **Authenticated** callers present a Bearer JWT validated by
  ``teamrag.auth`` (signature, issuer, audience, expiry against the IdP's
  JWKS). Their identity carries a ``groups`` tuple pulled from the token's
  ``groups`` claim; retrieval widens the Qdrant filter to tier-0 **union**
  those group tags.
- **Group name == ACL tag.** A chunk's ``acl_tags`` payload is matched
  directly against the caller's token groups — there is no separate mapping
  table consulted at query time (mapping happens at ingest/sync time, see
  ``teamrag.sync``). Because group names are treated as ACL tags, the
  ``tier-*`` prefix is reserved for system-assigned tiers (``tier-0``,
  ``tier-1``, ...); ``teamrag.auth._normalize_groups`` strips any IdP group
  literally named ``tier-*`` before it reaches this module, so it can never
  be used as a skeleton key for tier-scoped content.
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Canonical public / engineering-wide tier tag — must match Postgres ``acl_tags.tag``
# and Qdrant payload field ``acl_tags`` string values.
TIER_0_TAG: str = "tier-0"
TIER_1_TAG: str = "tier-1"


class AclFilterMode(str, enum.Enum):
    """How retrieval constrains Qdrant results."""

    UNAUTHENTICATED_TIER_0 = "unauthenticated_tier0"
    AUTHENTICATED_GROUPS = "authenticated_groups"


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


def qdrant_filter_for_mode(mode: AclFilterMode, user_groups: "Sequence[str]" = ()):
    """Build a Qdrant ``Filter`` for the given ACL mode (lazy qdrant imports).

    Missing ``acl_tags`` is treated as tier-0 for backward compatibility.
    ``AUTHENTICATED_GROUPS`` widens visibility to tier-0 plus the caller's
    group tags; with no groups it degrades to the tier-0 filter.
    """
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        IsEmptyCondition,
        MatchAny,
        MatchValue,
        PayloadField,
    )

    should = [
        FieldCondition(key="acl_tags", match=MatchValue(value=TIER_0_TAG)),
        IsEmptyCondition(is_empty=PayloadField(key="acl_tags")),
    ]
    if mode is AclFilterMode.UNAUTHENTICATED_TIER_0:
        return Filter(should=should)
    if mode is AclFilterMode.AUTHENTICATED_GROUPS:
        groups = [str(g) for g in user_groups if str(g)]
        if groups:
            should.append(FieldCondition(key="acl_tags", match=MatchAny(any=groups)))
        return Filter(should=should)
    raise ValueError(f"Unsupported ACL filter mode: {mode!r}")


def qdrant_filter_scroll_by_source_url(
    source_url_variants: list[str],
    mode: AclFilterMode,
    user_groups: "Sequence[str]" = (),
):
    """Qdrant scroll filter: ``source_url`` matches one of *variants* and ACL *mode* applies."""
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    tier = qdrant_filter_for_mode(mode, user_groups)
    return Filter(
        must=[
            FieldCondition(
                key="source_url",
                match=MatchAny(any=source_url_variants),
            ),
            tier,
        ],
    )


def resolve_acl_context(identity) -> "tuple[AclFilterMode, tuple[str, ...]]":
    """Map an optional ``UserIdentity`` to (filter mode, group tags)."""
    if identity is None or not getattr(identity, "groups", ()):
        return (AclFilterMode.UNAUTHENTICATED_TIER_0, ())
    return (AclFilterMode.AUTHENTICATED_GROUPS, tuple(identity.groups))


def resolve_acl_filter_mode_from_request(_request: Any) -> AclFilterMode:
    """Pick Qdrant ACL filter mode from the incoming HTTP request.

    Phase 5: every caller is treated as unauthenticated; tier-0-only filtering
    always applies. Future phases may inspect JWT / headers here.
    """
    return AclFilterMode.UNAUTHENTICATED_TIER_0


def log_acl_filter_mode(mode: AclFilterMode) -> None:
    """Log non-sensitive ACL retrieval mode for operators."""
    logger.debug("Qdrant ACL filter mode: %s", mode.value)

"""Ingest-side lookup of resource → ACL tags (Phase 7 squad ACLs).

Connectors call ``apply_acl_mapping`` on each chunk before upsert; chunks with
no mapping keep their default tags (tier-0 via ``merge_acl_tags_for_ingest``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RESOURCE_KEY_FIELDS: dict[str, str] = {
    "github": "repo",
    "confluence": "space_key",
    "teams": "channel_id",
    "webex": "space_id",
}


async def load_acl_mappings(session) -> dict[tuple[str, str], list[str]]:
    """Load all resource→tags mappings from Postgres into a lookup dict."""
    from sqlalchemy import select

    from teamrag.db.models import ResourceAclMapping

    result = await session.execute(select(ResourceAclMapping))
    mappings: dict[tuple[str, str], list[str]] = {}
    for row in result.scalars():
        mappings[(row.source_type, row.resource_key)] = list(row.acl_tags)
    logger.info("Loaded %d resource ACL mappings", len(mappings))
    return mappings


def resource_key_for_chunk(source_type: str, chunk: dict) -> str | None:
    field = _RESOURCE_KEY_FIELDS.get(source_type)
    if field is None:
        return None
    value = chunk.get(field)
    return str(value) if value else None


def apply_acl_mapping(
    chunk: dict,
    source_type: str,
    mappings: dict[tuple[str, str], list[str]],
) -> None:
    key = resource_key_for_chunk(source_type, chunk)
    if key is None:
        return
    tags = mappings.get((source_type, key))
    if tags:
        chunk["acl_tags"] = list(tags)

"""Refresh resource_acl_mappings from Keycloak group attributes (Phase 7).

Nightly-job entry point: ``python -m teamrag.sync``. Each Keycloak group may
carry multi-valued attributes ``repos`` / ``spaces`` / ``channels`` / ``rooms``
naming the resources its members may see; each named resource is mapped to
``[<group-name>, "tier-1"]``.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_ATTRIBUTE_SOURCE_TYPES: dict[str, str] = {
    "repos": "github",
    "spaces": "confluence",
    "channels": "teams",
    "rooms": "webex",
}

MappingRow = tuple[str, str, list[str]]


def mappings_from_groups(groups: list[dict]) -> list[MappingRow]:
    rows: list[MappingRow] = []
    for group in groups:
        name = str(group.get("name", "")).strip()
        if not name:
            continue
        tags = [name, "tier-1"]
        attributes = group.get("attributes") or {}
        for attr, source_type in _ATTRIBUTE_SOURCE_TYPES.items():
            for resource_key in attributes.get(attr, []) or []:
                resource_key = str(resource_key).strip()
                if resource_key:
                    rows.append((source_type, resource_key, list(tags)))
    return rows


def aggregate_mapping_rows(rows: list[MappingRow]) -> list[MappingRow]:
    """Merge tags for duplicate ``(source_type, resource_key)`` keys.

    Two groups may reference the same resource; without merging, the later
    row's tags would clobber the earlier group's within a single sync run.
    Tag order is stable (first occurrence wins) and deduped.
    """
    merged: dict[tuple[str, str], list[str]] = {}
    for source_type, resource_key, tags in rows:
        existing = merged.setdefault((source_type, resource_key), [])
        for tag in tags:
            if tag not in existing:
                existing.append(tag)
    return [(st, rk, tags) for (st, rk), tags in merged.items()]


def parse_seed_arg(value: str) -> MappingRow:
    error = ValueError(
        f"--seed must look like source_type:resource_key:tag1,tag2 — got {value!r}"
    )
    head, sep, rest = value.partition(":")
    if not sep:
        raise error
    resource_key, sep, tags_csv = rest.rpartition(":")
    if not sep:
        raise error
    source_type = head
    if not source_type or not resource_key or not tags_csv:
        raise error
    tags = [t.strip() for t in tags_csv.split(",") if t.strip()]
    if not tags:
        raise ValueError(f"--seed has no tags: {value!r}")
    return (source_type, resource_key, tags)


async def fetch_keycloak_groups(
    *, base_url: str, realm: str, admin_user: str, admin_password: str
) -> list[dict]:
    """Fetch all groups (with attributes) via the Keycloak admin REST API."""
    token_url = f"{base_url.rstrip('/')}/realms/master/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        groups_response = await client.get(
            f"{base_url.rstrip('/')}/admin/realms/{realm}/groups",
            params={"briefRepresentation": "false"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        groups_response.raise_for_status()
        return groups_response.json()


async def upsert_mappings(session, rows: list[MappingRow]) -> int:
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.db.models import ResourceAclMapping

    count = 0
    for source_type, resource_key, tags in aggregate_mapping_rows(rows):
        stmt = (
            pg_insert(ResourceAclMapping)
            .values(source_type=source_type, resource_key=resource_key, acl_tags=tags)
            .on_conflict_do_update(
                constraint="uq_resource_acl_mappings_type_key",
                set_={"acl_tags": tags, "updated_at": sa.func.now()},
            )
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    return count


def keys_to_prune(
    existing_keys: "set[tuple[str, str]]", fetched_keys: "set[tuple[str, str]]"
) -> "set[tuple[str, str]]":
    """Pure: mapping keys present in the DB but absent from a fresh fetch.

    Used only by the **full sync** path (not ``--seed``): a full Keycloak
    fetch is authoritative for "what should exist right now," so anything
    previously upserted but no longer reported by any group's attributes has
    had its access revoked and must be deleted — otherwise a stale mapping
    row keeps granting access to a resource nothing points at anymore, and
    revocation never converges. ``--seed`` writes a partial, hand-picked set
    of rows and is never a full picture, so it must never prune.
    """
    return existing_keys - fetched_keys


async def prune_stale_mappings(session, fetched_rows: list[MappingRow]) -> int:
    """Delete ``resource_acl_mappings`` rows absent from ``fetched_rows``.

    Call only after a full Keycloak sync's ``upsert_mappings`` has run in the
    same sync pass. Never call this for ``--seed`` runs.
    """
    import sqlalchemy as sa

    from teamrag.db.models import ResourceAclMapping

    fetched_keys = {(source_type, resource_key) for source_type, resource_key, _tags in fetched_rows}

    result = await session.execute(
        sa.select(ResourceAclMapping.source_type, ResourceAclMapping.resource_key)
    )
    existing_keys = {(row[0], row[1]) for row in result.all()}

    stale = keys_to_prune(existing_keys, fetched_keys)
    if not stale:
        return 0

    for source_type, resource_key in stale:
        await session.execute(
            sa.delete(ResourceAclMapping).where(
                ResourceAclMapping.source_type == source_type,
                ResourceAclMapping.resource_key == resource_key,
            )
        )
    await session.commit()
    return len(stale)

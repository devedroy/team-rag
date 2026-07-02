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


def parse_seed_arg(value: str) -> MappingRow:
    parts = value.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"--seed must look like source_type:resource_key:tag1,tag2 — got {value!r}"
        )
    source_type, resource_key, tags_csv = parts
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
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.db.models import ResourceAclMapping

    count = 0
    for source_type, resource_key, tags in rows:
        stmt = (
            pg_insert(ResourceAclMapping)
            .values(source_type=source_type, resource_key=resource_key, acl_tags=tags)
            .on_conflict_do_update(
                constraint="uq_resource_acl_mappings_type_key",
                set_={"acl_tags": tags},
            )
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    return count

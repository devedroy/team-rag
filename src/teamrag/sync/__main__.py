"""CLI: python -m teamrag.sync [--seed source_type:resource_key:tag1,tag2 ...]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _run(seed_rows) -> None:
    from teamrag.config import settings
    from teamrag.db.session import get_session
    from teamrag.sync import (
        fetch_keycloak_groups,
        mappings_from_groups,
        prune_stale_mappings,
        upsert_mappings,
    )

    # --seed writes a partial, hand-picked set of rows and is never a full
    # picture of what should exist — only a full Keycloak fetch is
    # authoritative enough to prune mappings that have fallen off.
    is_full_sync = not seed_rows

    if seed_rows:
        rows = seed_rows
        logger.info("Seeding %d mapping(s) from CLI args", len(rows))
    else:
        if not settings.KEYCLOAK_ADMIN_PASSWORD:
            logger.error("KEYCLOAK_ADMIN_PASSWORD is not set; cannot sync from Keycloak.")
            sys.exit(1)
        groups = await fetch_keycloak_groups(
            base_url=settings.KEYCLOAK_BASE_URL,
            realm=settings.KEYCLOAK_REALM,
            admin_user=settings.KEYCLOAK_ADMIN_USER,
            admin_password=settings.KEYCLOAK_ADMIN_PASSWORD,
        )
        rows = mappings_from_groups(groups)
        logger.info("Fetched %d group(s) → %d mapping(s) from Keycloak", len(groups), len(rows))

    async for session in get_session():
        written = await upsert_mappings(session, rows)
        logger.info("Upserted %d resource ACL mapping(s)", written)
        if is_full_sync:
            pruned = await prune_stale_mappings(session, rows)
            logger.info("Pruned %d stale resource ACL mapping(s)", pruned)


def main() -> None:
    from teamrag.sync import parse_seed_arg

    parser = argparse.ArgumentParser(description="Refresh resource_acl_mappings")
    parser.add_argument(
        "--seed",
        action="append",
        default=[],
        metavar="SOURCE_TYPE:RESOURCE_KEY:TAG1,TAG2",
        help="Upsert one mapping directly instead of syncing from Keycloak (repeatable)",
    )
    args = parser.parse_args()
    try:
        seed_rows = [parse_seed_arg(s) for s in args.seed]
    except ValueError as exc:
        parser.error(str(exc))
    asyncio.run(_run(seed_rows))


if __name__ == "__main__":
    main()

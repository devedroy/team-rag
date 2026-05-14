"""Ingestion pipeline: chunk → embed → upsert."""

from __future__ import annotations

import hashlib
import logging

from markdownify import markdownify
from llama_index.core.node_parser import MarkdownNodeParser

from teamrag.acl import TIER_0_TAG

logger = logging.getLogger(__name__)

# Optional keys copied from ingest chunk dicts into Qdrant payload (never silently drop).
OPTIONAL_QDRANT_PAYLOAD_KEYS: tuple[str, ...] = (
    "space_key",
    "page_id",
    "pr_number",
    "pr_title",
    "author",
    "merged_at",
    "repo",
    # Phase 6 chat (Teams / Webex)
    "source",
    "tenant_id",
    "org_id",
    "channel_id",
    "space_id",
    "channel_name",
    "space_title",
    "thread_id",
    "participants",
    "reply_count",
    "reaction_count",
    "has_code_block",
    "last_activity_at",
)


def _qdrant_payload_extras(chunk: dict) -> dict:
    out: dict = {}
    for key in OPTIONAL_QDRANT_PAYLOAD_KEYS:
        if key in chunk and chunk[key] is not None:
            out[key] = chunk[key]
    return out


def html_to_markdown(html: str) -> str:
    """Convert Confluence body.storage HTML to Markdown."""
    return markdownify(html, heading_style="ATX").strip()


def chunk_document(page: dict, confluence_base_url: str) -> list[dict]:
    """Split a Confluence page into structured chunks with metadata."""
    page_id = page["id"]
    page_title = page.get("title", "")
    space_key = page.get("space", {}).get("key", "")
    last_updated = page.get("version", {}).get("when", "")
    web_ui_path = page.get("_links", {}).get("webui", "")
    url = f"{confluence_base_url.rstrip('/')}{web_ui_path}"

    html = page.get("body", {}).get("storage", {}).get("value", "")
    markdown = html_to_markdown(html)

    if not markdown.strip():
        logger.warning("Page %s (%s) produced empty markdown — skipping", page_id, page_title)
        return []

    parser = MarkdownNodeParser()
    nodes = parser.get_nodes_from_documents(
        [_make_llama_doc(markdown, page_id)]
    )

    chunks = []
    for idx, node in enumerate(nodes):
        text = node.get_content().strip()
        if not text:
            continue
        chunk_id = _stable_chunk_id(page_id, idx)
        chunks.append({
            "chunk_id": chunk_id,
            "content": text,
            "page_id": page_id,
            "page_title": page_title,
            "url": url,
            "space_key": space_key,
            "last_updated": last_updated,
            "chunk_index": idx,
            "acl_tags": [TIER_0_TAG],
        })

    logger.info("Page %s chunked into %d chunks", page_id, len(chunks))
    return chunks


def _stable_chunk_id(page_id: str, chunk_index: int) -> str:
    """SHA256-based stable chunk ID — same across re-runs."""
    key = f"{page_id}:{chunk_index}"
    return hashlib.sha256(key.encode()).hexdigest()


def _make_llama_doc(markdown: str, page_id: str):
    from llama_index.core import Document
    return Document(text=markdown, doc_id=page_id)


async def embed_chunks(chunks: list[dict], tei_url: str) -> list[list[float]]:
    """Embed chunk contents via TEI /embed endpoint in batches of 32.

    Returns one embedding vector per chunk, in the same order.
    """
    import httpx

    texts = [c["content"] for c in chunks]
    batch_size = 32
    all_vectors: list[list[float]] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await client.post(
                f"{tei_url.rstrip('/')}/embed",
                json={"inputs": batch},
            )
            response.raise_for_status()
            all_vectors.extend(response.json())

    return all_vectors


async def upsert_to_qdrant(
    chunks: list[dict],
    vectors: list[list[float]],
    qdrant_client,
    collection_name: str,
) -> None:
    """Upsert chunk vectors into Qdrant — idempotent (existing IDs are overwritten).

    Chunk dicts may originate from Confluence (``url``, ``page_title``), GitHub
    (``source_url``, ``pr_title``), or Phase 6 chat threads (``source``,
    ``thread_id``, Teams/Webex metadata keys — see ``OPTIONAL_QDRANT_PAYLOAD_KEYS``).
    """
    from qdrant_client.models import PointStruct

    from teamrag.acl import merge_acl_tags_for_ingest

    points = []
    for chunk, vector in zip(chunks, vectors):
        hex_id = chunk["chunk_id"]
        point_id = int(hex_id[:16], 16)
        payload: dict = {
            "content": chunk["content"],
            "source_url": chunk.get("url", chunk.get("source_url", "")),
            "page_title": chunk.get("page_title", ""),
            "last_updated": chunk.get("last_updated", ""),
            "chunk_index": chunk["chunk_index"],
            "acl_tags": merge_acl_tags_for_ingest(chunk),
        }
        payload.update(_qdrant_payload_extras(chunk))
        points.append(
            PointStruct(id=point_id, vector=vector, payload=payload)
        )

    await qdrant_client.upsert(collection_name=collection_name, points=points)
    logger.info("Upserted %d points into Qdrant collection '%s'", len(points), collection_name)


async def write_to_postgres(page: dict, chunks: list[dict], session, confluence_base_url: str) -> None:
    """Upsert one Source row and one Chunk row per chunk into Postgres."""
    from datetime import datetime

    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.acl import merge_acl_tags_for_ingest
    from teamrag.db.models import AclTag, Source, Chunk as ChunkModel

    page_id = page["id"]
    page_title = page.get("title", "")
    web_ui_path = page.get("_links", {}).get("webui", "")
    source_url = f"{confluence_base_url.rstrip('/')}{web_ui_path}"
    last_updated_str = page.get("version", {}).get("when", "")
    last_updated = None
    if last_updated_str:
        try:
            last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    # Upsert Source row (keyed on source_url)
    stmt = pg_insert(Source).values(
        source_type="confluence",
        source_url=source_url,
        page_title=page_title,
        last_updated=last_updated,
    ).on_conflict_do_update(
        index_elements=["source_url"],
        set_={"page_title": page_title, "last_updated": last_updated},
    ).returning(Source.id)
    result = await session.execute(stmt)
    source_id = result.scalar_one()

    # Upsert Chunk rows + ACL tags (tier-0 for public engineering-wide corpus)
    for chunk in chunks:
        chunk_stmt = (
            pg_insert(ChunkModel)
            .values(
                source_id=source_id,
                content=chunk["content"],
                chunk_index=chunk["chunk_index"],
                chunk_metadata={
                    "space_key": chunk["space_key"],
                    "page_id": chunk["page_id"],
                    "last_updated": chunk["last_updated"],
                },
            )
            .on_conflict_do_update(
                constraint="uq_chunks_source_chunk_index",
                set_={"content": chunk["content"]},
            )
            .returning(ChunkModel.id)
        )
        res = await session.execute(chunk_stmt)
        chunk_uuid = res.scalar_one()
        tags = merge_acl_tags_for_ingest(chunk)
        await session.execute(delete(AclTag).where(AclTag.chunk_id == chunk_uuid))
        for tag in tags:
            await session.execute(
                pg_insert(AclTag).values(chunk_id=chunk_uuid, tag=tag)
            )

    await session.commit()
    logger.info("Wrote source + %d chunks to Postgres for page %s", len(chunks), page_id)


async def write_chat_thread_to_postgres(
    *,
    source_type: str,
    chunk: dict,
    chunk_metadata: dict,
    session,
) -> None:
    """Upsert one Source (thread URL) and one Chunk (index 0) for a chat thread."""
    from datetime import datetime

    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.acl import merge_acl_tags_for_ingest
    from teamrag.db.models import AclTag, Chunk as ChunkModel, Source

    source_url = chunk["source_url"]
    page_title = chunk.get("page_title", "")
    last_updated_str = chunk.get("last_updated") or chunk.get("last_activity_at")
    last_updated: datetime | None = None
    if last_updated_str:
        try:
            last_updated = datetime.fromisoformat(str(last_updated_str).replace("Z", "+00:00"))
        except ValueError:
            last_updated = None

    stmt = (
        pg_insert(Source)
        .values(
            source_type=source_type,
            source_url=source_url,
            page_title=page_title,
            last_updated=last_updated,
        )
        .on_conflict_do_update(
            index_elements=["source_url"],
            set_={"page_title": page_title, "last_updated": last_updated},
        )
        .returning(Source.id)
    )
    result = await session.execute(stmt)
    source_id = result.scalar_one()

    chunk_stmt = (
        pg_insert(ChunkModel)
        .values(
            source_id=source_id,
            content=chunk["content"],
            chunk_index=0,
            chunk_metadata=chunk_metadata,
        )
        .on_conflict_do_update(
            constraint="uq_chunks_source_chunk_index",
            set_={"content": chunk["content"], "metadata": chunk_metadata},
        )
        .returning(ChunkModel.id)
    )
    res = await session.execute(chunk_stmt)
    chunk_uuid = res.scalar_one()
    tags = merge_acl_tags_for_ingest(chunk)
    await session.execute(delete(AclTag).where(AclTag.chunk_id == chunk_uuid))
    for tag in tags:
        await session.execute(pg_insert(AclTag).values(chunk_id=chunk_uuid, tag=tag))

    await session.commit()
    logger.info("Wrote chat thread source + chunk to Postgres: %s", source_url)

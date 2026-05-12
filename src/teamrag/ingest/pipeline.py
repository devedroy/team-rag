"""Ingestion pipeline: chunk → embed → upsert."""

from __future__ import annotations

import hashlib
import logging

from markdownify import markdownify
from llama_index.core.node_parser import MarkdownNodeParser

logger = logging.getLogger(__name__)


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
    """Upsert chunk vectors into Qdrant — idempotent (existing IDs are overwritten)."""
    from qdrant_client.models import PointStruct

    points = []
    for chunk, vector in zip(chunks, vectors):
        # Convert first 16 hex chars of SHA256 chunk_id to an unsigned 64-bit int for Qdrant
        hex_id = chunk["chunk_id"]
        point_id = int(hex_id[:16], 16)
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "content": chunk["content"],
                    "source_url": chunk["url"],
                    "page_title": chunk["page_title"],
                    "last_updated": chunk["last_updated"],
                    "space_key": chunk["space_key"],
                    "page_id": chunk["page_id"],
                    "chunk_index": chunk["chunk_index"],
                },
            )
        )

    await qdrant_client.upsert(collection_name=collection_name, points=points)
    logger.info("Upserted %d points into Qdrant collection '%s'", len(points), collection_name)


async def write_to_postgres(page: dict, chunks: list[dict], session, confluence_base_url: str) -> None:
    """Upsert one Source row and one Chunk row per chunk into Postgres."""
    from datetime import datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from teamrag.db.models import Source, Chunk as ChunkModel

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

    # Upsert Chunk rows
    for chunk in chunks:
        chunk_stmt = pg_insert(ChunkModel).values(
            source_id=source_id,
            content=chunk["content"],
            chunk_index=chunk["chunk_index"],
            chunk_metadata={
                "space_key": chunk["space_key"],
                "page_id": chunk["page_id"],
                "last_updated": chunk["last_updated"],
            },
        ).on_conflict_do_update(
            constraint="uq_chunks_source_chunk_index",
            set_={"content": chunk["content"]},
        )
        await session.execute(chunk_stmt)

    await session.commit()
    logger.info("Wrote source + %d chunks to Postgres for page %s", len(chunks), page_id)

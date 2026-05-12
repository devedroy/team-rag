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

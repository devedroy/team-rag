"""OpenAI-compatible chat completions endpoint with RAG-augmented prompting."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from teamrag.api.query import ChunkResult, _embed_query
from teamrag.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_SYSTEM_PROMPT_TEMPLATE = """\
You are a helpful assistant for an engineering team. Answer questions using ONLY the provided context chunks.
For each factual claim, cite the source with [Source N]. At the end of your response, list:
[Source 1]: <url>
[Source 2]: <url>
If no relevant context is found, say so clearly.

Context:
{context_blocks}"""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    stream: bool = False
    top_k: int = Field(default=5, ge=1, le=100)


def _build_context_blocks(chunks: list[ChunkResult]) -> str:
    blocks: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        blocks.append(
            f"[{i}] Title: {chunk.page_title}\n"
            f"URL: {chunk.source_url}\n"
            f"{chunk.content}"
        )
    return "\n\n".join(blocks) if blocks else "(no relevant context found)"


def _build_augmented_messages(
    original_messages: list[ChatMessage],
    chunks: list[ChunkResult],
) -> list[dict[str, str]]:
    context_blocks = _build_context_blocks(chunks)
    system_content = _SYSTEM_PROMPT_TEMPLATE.format(context_blocks=context_blocks)

    augmented: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    for msg in original_messages:
        if msg.role != "system":
            augmented.append({"role": msg.role, "content": msg.content})
    return augmented


async def _retrieve_chunks(query: str, top_k: int, qdrant_client: Any) -> list[ChunkResult]:
    try:
        vector = await _embed_query(query, settings.TEI_URL)
    except Exception as exc:
        logger.warning("TEI embedding failed during chat retrieval: %s", exc)
        return []

    try:
        results = await qdrant_client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant query failed during chat retrieval: %s", exc)
        return []

    chunks: list[ChunkResult] = []
    for hit in results.points:
        payload = hit.payload or {}
        chunks.append(
            ChunkResult(
                content=payload.get("content", ""),
                source_url=payload.get("source_url", ""),
                page_title=payload.get("page_title", ""),
                score=hit.score,
            )
        )
    return chunks


async def _stream_llm_response(
    augmented_messages: list[dict[str, str]],
    model: str,
) -> StreamingResponse:
    llm_url = f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
    }
    payload = {
        "model": model or settings.LLM_MODEL,
        "messages": augmented_messages,
        "stream": True,
    }

    async def event_generator():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", llm_url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _non_streaming_llm_response(
    augmented_messages: list[dict[str, str]],
    model: str,
) -> dict[str, Any]:
    llm_url = f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
    }
    payload = {
        "model": model or settings.LLM_MODEL,
        "messages": augmented_messages,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(llm_url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


def _empty_streaming_response(content: str) -> StreamingResponse:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    async def generator():
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": settings.LLM_MODEL,
            "choices": [{"delta": {"content": content}, "index": 0, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        done_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": settings.LLM_MODEL,
            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


def _empty_non_streaming_response(content: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": settings.LLM_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    if not settings.LLM_BASE_URL:
        msg = "LLM_BASE_URL is not configured. Set it in .env to enable chat completions."
        if request.stream:
            return _empty_streaming_response(msg)
        return _empty_non_streaming_response(msg)

    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found in messages list.")
    retrieval_query = user_messages[-1].content

    qdrant_client = getattr(http_request.app.state, "qdrant_client", None)
    if qdrant_client is not None:
        chunks = await _retrieve_chunks(retrieval_query, request.top_k, qdrant_client)
    else:
        logger.warning("Qdrant client unavailable — proceeding without context chunks")
        chunks = []

    augmented_messages = _build_augmented_messages(request.messages, chunks)
    model = request.model or settings.LLM_MODEL

    if request.stream:
        return await _stream_llm_response(augmented_messages, model)
    return await _non_streaming_llm_response(augmented_messages, model)

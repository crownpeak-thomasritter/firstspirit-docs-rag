"""Message routes — POST /api/conversations/{conv_id}/messages.

Orchestrates the tool-driven RAG flow for the FirstSpirit documentation
corpus. Compared with the YouTube-era donor:

* No auth, no rate-limit. The pivot uses a single anonymous identity
  (``DEFAULT_USER_ID`` from :mod:`backend.routes.conversations`).
* Citations are scoped to documents, not videos: same-document chunks
  collapse via :func:`_collapse_by_document` after the ``is_cited`` pass.
* The refusal probe matches the enforced phrase
  ``"the documentation library does not cover that topic"`` plus a few
  general refusal phrasings; we removed every video-specific pattern.
* The tool whitelist is built from document ids returned by
  :func:`repository.list_documents`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from backend.config import CITATIONS_MAX_COUNT, LLM_TOOLS_ENABLED, LLM_TOOLS_MAX_PER_TURN
from backend.db import repository
from backend.llm.openrouter import stream_chat
from backend.rag.citations import CitationMarkerStripper, extract_cited_chunk_ids
from backend.rag.tools import TOOL_SCHEMAS, execute_tool, serialize_tool_result
from backend.routes.conversations import DEFAULT_USER_ID

logger = logging.getLogger(__name__)

router = APIRouter()


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, description="Message content (non-empty)")

    @field_validator("content", mode="before")
    @classmethod
    def content_not_whitespace_only(cls, v: str) -> str:
        if isinstance(v, str) and v.strip() == "":
            raise ValueError("content must not be empty or whitespace-only")
        return v


@router.post("/conversations/{conv_id}/messages")
async def create_message(conv_id: str, body: MessageCreate) -> StreamingResponse:
    """Send a user message and stream the RAG-grounded assistant response."""
    user_id = DEFAULT_USER_ID

    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_content = body.content.strip()
    inserted = await repository.create_message(
        conversation_id=conv_id, user_id=user_id, role="user", content=user_content
    )
    if inserted is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    all_messages = await repository.list_messages(conv_id, user_id=user_id)
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in all_messages]

    source_citations: list[dict] = []
    tool_chunks_acc: list[dict] = []
    embedding_cache: dict[str, list[float]] = {}
    tools_param: list[dict] | None = None
    executor = None
    max_tool_calls = 0
    if LLM_TOOLS_ENABLED:
        try:
            all_docs = await repository.list_documents()
            document_id_whitelist: set[str] = {d["id"] for d in all_docs if d.get("id")}
        except Exception as exc:
            logger.warning(
                "Failed to load document whitelist; get_document calls will be unguarded: %s",
                exc,
            )
            document_id_whitelist = set()

        async def _executor(name: str, raw_args: str) -> str:
            whitelist = document_id_whitelist if document_id_whitelist else None
            result = await execute_tool(
                name,
                raw_args,
                document_id_whitelist=whitelist,
                embedding_cache=embedding_cache,
            )
            if result.get("ok") and result.get("chunks"):
                tool_chunks_acc.extend(result["chunks"])
            return serialize_tool_result(result)

        tools_param = TOOL_SCHEMAS
        executor = _executor
        max_tool_calls = LLM_TOOLS_MAX_PER_TURN

    async def event_generator() -> AsyncGenerator[str, None]:
        full_response: list[str] = []
        final_text_buf: list[str] = []
        marker_stripper = CitationMarkerStripper()
        try:
            async for sse_chunk in stream_chat(
                llm_messages,
                tools=tools_param,
                tool_executor=executor,
                max_tool_calls=max_tool_calls,
                final_text_out=final_text_buf,
            ):
                if sse_chunk == "data: [DONE]\n\n":
                    tail = marker_stripper.flush()
                    if tail:
                        tail_chunk = f"data: {json.dumps(tail)}\n\n"
                        full_response.append(tail_chunk)
                        yield tail_chunk

                    if tool_chunks_acc:
                        seen: set[str] = set()
                        for tc in tool_chunks_acc:
                            tc_id = tc.get("chunk_id")
                            if tc_id and tc_id not in seen:
                                source_citations.append(tc)
                                seen.add(tc_id)

                    if source_citations:
                        final_text_raw = final_text_buf[0] if final_text_buf else ""
                        cited_ids = extract_cited_chunk_ids(final_text_raw)
                        for chunk in source_citations:
                            chunk["is_cited"] = chunk.get("chunk_id") in cited_ids
                        source_citations[:] = _collapse_by_document(source_citations)
                        cited = [c for c in source_citations if c.get("is_cited")]
                        uncited = [c for c in source_citations if not c.get("is_cited")]
                        source_citations[:] = cited + uncited[:CITATIONS_MAX_COUNT]

                    if source_citations:
                        final_text = (
                            final_text_buf[0]
                            if final_text_buf
                            else _extract_text_from_sse(full_response)
                        )
                        if not _is_refusal(final_text):
                            sources_json = json.dumps(source_citations)
                            yield f"event: sources\ndata: {sources_json}\n\n"
                    full_response.append(sse_chunk)
                    yield sse_chunk
                    continue

                stripped = _strip_markers_from_sse_chunk(sse_chunk, marker_stripper)
                if stripped is None:
                    continue
                full_response.append(stripped)
                yield stripped
        finally:
            assistant_text = _extract_text_from_sse(full_response)
            if assistant_text:
                refusal_check_text = final_text_buf[0] if final_text_buf else assistant_text
                sources_to_persist: list[dict] | None = (
                    None
                    if not source_citations or _is_refusal(refusal_check_text)
                    else source_citations
                )
                try:
                    await asyncio.shield(
                        repository.create_message(
                            conversation_id=conv_id,
                            user_id=user_id,
                            role="assistant",
                            content=assistant_text,
                            sources=sources_to_persist,
                        )
                    )
                except asyncio.CancelledError:
                    logger.info(
                        "Client disconnected mid-persist; shielded create_message "
                        "continues in background"
                    )
                except Exception as exc:
                    logger.error("Failed to persist assistant message: %s", exc)
                    raise
                try:
                    await asyncio.shield(
                        _maybe_set_conversation_title(conv_id, user_id, user_content)
                    )
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("Failed to update conversation title: %s", exc)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_markers_from_sse_chunk(sse_chunk: str, stripper: CitationMarkerStripper) -> str | None:
    """Run a token chunk through ``stripper``; return a rewritten chunk or
    ``None`` if the entire token was held back as a partial marker. Non-token
    chunks (events, errors) pass through unchanged.
    """
    if not sse_chunk.startswith("data: "):
        return sse_chunk
    payload = sse_chunk[len("data: ") :].rstrip("\n")
    if not payload or payload == "[DONE]" or payload.startswith('{"error"'):
        return sse_chunk
    try:
        decoded = json.loads(payload)
    except ValueError:
        decoded = payload
    if not isinstance(decoded, str):
        return sse_chunk
    safe = stripper.feed(decoded)
    if not safe:
        return None
    return f"data: {json.dumps(safe)}\n\n"


def _extract_text_from_sse(sse_chunks: list[str]) -> str:
    """Reconstruct assistant text from a list of SSE event strings."""
    tokens: list[str] = []
    for chunk in sse_chunks:
        if not chunk.startswith("data: "):
            continue
        content = chunk[len("data: ") :].rstrip("\n")
        if not content or content == "[DONE]" or content.startswith('{"error"'):
            continue
        try:
            decoded = json.loads(content)
            if isinstance(decoded, str):
                tokens.append(decoded)
        except ValueError:
            tokens.append(content)
    return "".join(tokens)


def _is_refusal(text: str) -> bool:
    """Detect when the assistant declined to answer because the question is
    outside the documentation corpus.

    The primary mechanism is the system prompt — the model is instructed to
    emit the exact phrase ``"the documentation library does not cover that
    topic"`` when refusing. The other patterns are a belt-and-suspenders
    fallback for paraphrases observed during evaluation; each is anchored
    enough that it won't plausibly appear in a substantive grounded answer.
    """
    refusal_patterns = (
        # Enforced phrase (system prompt instructs the model to emit this).
        "the documentation library does not cover that topic",
        # Library-anchored denials.
        "the documentation library doesn't cover",
        "the documentation library does not cover",
        "documentation library doesn't contain",
        "documentation library does not contain",
        "not covered in the documentation",
        "not part of the documentation",
        # Search-result framings (first-person to avoid hits on partial answers).
        "my search of the documentation didn't return",
        "my search of the documentation did not return",
        "search of the documentation didn't return",
        "search of the documentation did not return",
        "i couldn't find this in the documentation",
        "i could not find this in the documentation",
        # General scope-mismatch phrasings.
        "outside the scope of the firstspirit documentation",
        "i can only answer questions about the firstspirit",
        "can only answer questions about firstspirit",
        "don't have information about that in the documentation",
        "do not have information about that in the documentation",
    )
    is_refusal = any(pattern.lower() in text.lower() for pattern in refusal_patterns)
    if is_refusal:
        logger.debug("Refusal detected in assistant response")
    return is_refusal


async def _maybe_set_conversation_title(
    conv_id: str, user_id: str, first_user_message: str
) -> None:
    """Auto-title a conversation from its first user message (simple truncation)."""
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        return
    if conv.get("title") == "New Conversation":
        if len(first_user_message) > 50:
            title = first_user_message[:47].strip() + "…"
        else:
            title = first_user_message.strip()
        await repository.update_conversation_title(conv_id, user_id=user_id, title=title)


def _collapse_by_document(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse multiple chunks from the same document into a single citation.

    After the ``is_cited`` pass, chunks from the same document are redundant
    in the UI — the user wants one clickable chip per document, not one per
    paragraph. ``is_cited`` is OR'd across the group. The representative is
    the earliest-cited chunk (or the lowest ``chunk_index`` overall if none
    were cited) so deep-links land near the most relevant section. Insertion
    order is preserved (first-seen document wins).
    """
    seen: dict[str, list[dict]] = {}
    for c in chunks:
        doc_id = c.get("document_id") or ""
        seen.setdefault(doc_id, []).append(c)

    collapsed: list[dict] = []
    for group in seen.values():
        cited_in_group = [c for c in group if c.get("is_cited")]
        if cited_in_group:
            representative = min(cited_in_group, key=lambda c: c.get("chunk_index", 0))
            is_cited = True
        else:
            representative = min(group, key=lambda c: c.get("chunk_index", 0))
            is_cited = False
        entry = dict(representative)
        entry["is_cited"] = is_cited
        collapsed.append(entry)

    return collapsed

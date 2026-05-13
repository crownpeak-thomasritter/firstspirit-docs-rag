"""LLM retrieval tools — FirstSpirit documentation edition.

All RAG retrieval is exposed as LLM tool calls — no pre-retrieval happens
before the model runs. The model chooses which strategy fits each question:

  - ``search_documents``           — hybrid (keyword + vector via RRF). Default.
  - ``keyword_search_documents``   — tsvector FTS only. Best for exact terms.
  - ``semantic_search_documents``  — pgvector cosine only. Best for paraphrases.
  - ``get_document``               — full body of one document.

Executors return a dict of shape
    {"ok": True, "text": <LLM-facing string>, "chunks": <citation-shaped list>}
on success, or ``{"ok": False, "error": <str>}`` on any failure. The caller
accumulates ``chunks`` into the SSE ``sources`` event so citation chips
reflect whatever the model actually read.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from backend.db import repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

SEARCH_DOCUMENTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": (
            "Hybrid search over the documentation library, combining keyword "
            "and semantic retrieval via Reciprocal Rank Fusion. This is the "
            "default and recommended strategy for most questions. Returns the "
            "most relevant chunks across all documents with deep-link "
            "citations to the exact heading."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — a question, phrase, or keyword.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max chunks to return (default 10, range 1-30).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

KEYWORD_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "keyword_search_documents",
        "description": (
            "Keyword/full-text search (Postgres tsvector). Best when the user "
            "uses exact terminology, proper nouns, acronyms, or technical "
            "terms likely to appear verbatim in the docs. Prefer "
            "`search_documents` unless you specifically need literal-term "
            "matching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to match."},
                "top_k": {"type": "integer", "description": "Max chunks to return (default 10)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

SEMANTIC_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "semantic_search_documents",
        "description": (
            "Semantic/vector search (pgvector cosine). Best for conceptual or "
            "paraphrased questions where the user's wording may not match the "
            "docs literally. Prefer `search_documents` unless you know "
            "terminology will diverge and need pure semantic matching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question or concept to search for."},
                "top_k": {"type": "integer", "description": "Max chunks to return (default 10)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

GET_DOCUMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_document",
        "description": (
            "Read the full body of one document. Call this when a search "
            "returned relevant-but-insufficient chunks and you need the whole "
            "page (or the section you saw plus its neighbours) to answer "
            "well. Expensive — cap 2 per turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": (
                        "Internal document_id. Must come from a prior search result or the catalog."
                    ),
                }
            },
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    SEARCH_DOCUMENTS_TOOL,
    KEYWORD_SEARCH_TOOL,
    SEMANTIC_SEARCH_TOOL,
    GET_DOCUMENT_TOOL,
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_args(raw: str | dict) -> dict | None:
    """Parse tool arguments. Returns None on invalid JSON."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp_top_k(value: Any, default: int = 10, maximum: int = 30) -> int:
    """Coerce ``top_k`` to a sane integer in ``[1, maximum]``."""
    try:
        k = int(value) if value is not None else default
    except (TypeError, ValueError):
        k = default
    return max(1, min(maximum, k))


async def _hydrate_chunks(raw_chunks: list[dict]) -> list[dict]:
    """Enrich raw repository chunks with document title/url and reshape to
    the canonical citation-chunk dict.

    Fetches document metadata for all unique document_ids concurrently to
    avoid serial DB round-trips when a search spans many documents.
    """
    if not raw_chunks:
        return []

    unique_ids = list({c.get("document_id", "") for c in raw_chunks if c.get("document_id")})

    async def _load(doc_id: str) -> tuple[str, dict[str, str | None]]:
        try:
            doc = await repository.get_document(doc_id)
        except Exception as exc:
            logger.warning("hydrate: get_document failed for %s: %s", doc_id, exc, exc_info=True)
            doc = None
        info = doc or {}
        return doc_id, {
            "title": info.get("title") or "Unknown Document",
            "url": info.get("url"),
            "content_path": info.get("content_path"),
            "source_type": info.get("source_type") or "firstspirit",
        }

    document_cache: dict[str, dict[str, str | None]] = dict(
        await asyncio.gather(*(_load(v) for v in unique_ids))
    )

    out: list[dict] = []
    for c in raw_chunks:
        doc_id = c.get("document_id", "")
        meta = document_cache.get(doc_id, {})
        out.append(
            {
                "chunk_id": c.get("id", c.get("chunk_id", "")),
                "content": c.get("content", ""),
                "document_id": doc_id,
                "document_title": meta.get("title") or "Unknown Document",
                "document_url": meta.get("url"),
                "document_content_path": meta.get("content_path"),
                "source_type": meta.get("source_type") or "firstspirit",
                "section_path": c.get("section_path") or [],
                "anchor": c.get("anchor"),
                # Preserve chunk_index so the small-to-big expansion in
                # rag/expansion.py can fetch in-document neighbours.
                "chunk_index": c.get("chunk_index", 0),
            }
        )
    return out


def _format_search_results(chunks: list[dict]) -> str:
    """Format chunks as the LLM-facing tool result text.

    Each chunk is prefixed with a literal ``[c:<chunk_id>]`` marker — the
    same form the model is instructed to emit when citing the chunk in its
    answer. Followed by the document title and the section-path breadcrumb
    so the model has the heading context to disambiguate near-duplicate
    chunks.
    """
    if not chunks:
        return "No relevant chunks found. Try a different query or strategy."
    parts: list[str] = []
    for c in chunks:
        title = c.get("document_title") or "Unknown Document"
        chunk_id = c.get("chunk_id") or ""
        section_path = c.get("section_path") or []
        breadcrumb = " › ".join(section_path) if section_path else ""
        marker = f"[c:{chunk_id}] " if chunk_id else ""
        header = f"{marker}{title}"
        if breadcrumb:
            header = f"{header} › {breadcrumb}"
        parts.append(f"{header}\n{c.get('content', '')}")
    return "\n\n---\n\n".join(parts)


_CANONICAL_CHUNK_KEYS = (
    "chunk_id",
    "content",
    "document_id",
    "document_title",
    "document_url",
    "document_content_path",
    "source_type",
    "section_path",
    "anchor",
    # chunk_index is required by rag/expansion.py to fetch in-document
    # neighbours; dropping it causes expansion to silently fall back to
    # unexpanded chunks.
    "chunk_index",
)

_LIST_CHUNK_KEYS = frozenset(("section_path",))
_INT_CHUNK_KEYS = frozenset(("chunk_index",))
_NULLABLE_CHUNK_KEYS = frozenset(("document_url", "document_content_path", "anchor"))


def _normalize_chunk_shape(chunk: dict) -> dict:
    """Project a chunk dict onto the canonical citation shape.

    Different retrieval paths produce slightly different dicts — hybrid
    retrieval adds an RRF ``score`` that the frontend doesn't use, while
    hydrate-produced chunks don't have it. Normalising before the dedup /
    merge in ``routes/messages.py`` keeps the ``sources`` SSE payload
    consistent regardless of which tool the model called.
    """

    def _default(key: str) -> object:
        if key in _LIST_CHUNK_KEYS:
            return []
        if key in _INT_CHUNK_KEYS:
            return 0
        if key in _NULLABLE_CHUNK_KEYS:
            return None
        return ""

    return {key: chunk.get(key, _default(key)) for key in _CANONICAL_CHUNK_KEYS}


async def _expand_with_neighbors(chunks: list[dict]) -> list[dict]:
    """Apply small-to-big neighbour expansion to already-normalised chunks.

    Wrapper that imports lazily, catches failures so a broken expansion path
    never poisons a whole tool result, and is a no-op when the window is 0
    (tests, or disabled).
    """
    from backend.config import RETRIEVAL_EXPANSION_WINDOW
    from backend.rag.expansion import expand_and_merge

    if RETRIEVAL_EXPANSION_WINDOW <= 0 or not chunks:
        return chunks
    try:
        return await expand_and_merge(chunks, window=RETRIEVAL_EXPANSION_WINDOW)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("chunk expansion failed, using unexpanded chunks: %s", exc, exc_info=True)
        return chunks


def _apply_per_document_cap(chunks: list[dict], max_per_document: int) -> list[dict]:
    """Limit how many chunks from any single document reach the final context.

    Walks chunks in input ranking order and drops a chunk once its document
    has already contributed ``max_per_document`` chunks. Preserves relative
    ordering. ``max_per_document <= 0`` is a no-op.
    """
    if max_per_document <= 0 or not chunks:
        return chunks

    per_document: dict[str, int] = defaultdict(int)
    kept: list[dict] = []
    for c in chunks:
        doc_id = c.get("document_id")
        if not doc_id:
            kept.append(c)
            continue
        if per_document[doc_id] >= max_per_document:
            continue
        kept.append(c)
        per_document[doc_id] += 1
    return kept


def _format_document(document: dict, chunks: list[dict], max_chars: int | None = None) -> str:
    """Render chunks of one document as a markdown-ish body for the LLM.

    Walks the document's chunks in ``chunk_index`` order, prefixing each
    with a ``[c:<id>]`` marker so the model can cite the right one. If
    ``max_chars`` is supplied and the rendered body would exceed it,
    truncate at the last complete chunk that fits and append a truncation
    marker so the model knows content was dropped.
    """
    title = document.get("title", "Unknown Document")
    header = f"# {title}\n"
    parts: list[str] = [header]
    char_count = len(header)
    kept_chunks = 0
    total_chunks = 0

    last_breadcrumb: str | None = None
    for c in chunks:
        total_chunks += 1
        chunk_id = c.get("chunk_id") or c.get("id") or ""
        content = (c.get("content") or "").strip()
        if not content:
            continue
        section_path = c.get("section_path") or []
        breadcrumb = " › ".join(section_path) if section_path else ""
        marker_prefix = f"[c:{chunk_id}] " if chunk_id else ""
        if breadcrumb and breadcrumb != last_breadcrumb:
            piece = f"{marker_prefix}{breadcrumb}\n\n{content}"
            last_breadcrumb = breadcrumb
        else:
            piece = f"{marker_prefix}{content}"
        # Account for the "\n\n" separator we will join with.
        addition = len(piece) + 2
        if max_chars is not None and char_count + addition > max_chars:
            break
        parts.append(piece)
        char_count += addition
        kept_chunks += 1
    if max_chars is not None and kept_chunks < total_chunks:
        dropped = total_chunks - kept_chunks
        parts.append(
            f"\n[document truncated — {dropped} more chunks omitted to stay "
            f"within the {max_chars}-character cap for tool responses]"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


async def _embed_query(query: str, cache: dict[str, list[float]] | None) -> list[float]:
    """Embed a query, optionally memoising the result within one turn."""
    from backend.rag.embeddings import embed_text

    if cache is not None and query in cache:
        return cache[query]
    embedding = await asyncio.to_thread(embed_text, query)
    if cache is not None:
        cache[query] = embedding
    return embedding


async def execute_search_hybrid(
    raw_arguments: str | dict,
    embedding_cache: dict[str, list[float]] | None = None,
    allowed_source_types: list[str] | None = None,
) -> dict[str, Any]:
    """Hybrid (keyword + semantic via RRF) search."""
    from backend.config import RETRIEVAL_MAX_PER_DOCUMENT
    from backend.rag.retriever_hybrid import retrieve_hybrid

    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing required parameter: query"}
    top_k = _clamp_top_k(args.get("top_k"))

    try:
        embedding = await _embed_query(query, embedding_cache)
        chunks = await retrieve_hybrid(
            query,
            embedding,
            top_k=top_k,
            allowed_source_types=allowed_source_types,
        )
    except Exception as exc:
        logger.warning("search_hybrid failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"search failed: {exc}"}

    chunks = _apply_per_document_cap(chunks, RETRIEVAL_MAX_PER_DOCUMENT)
    chunks = [_normalize_chunk_shape(c) for c in chunks]
    chunks = await _expand_with_neighbors(chunks)
    return {"ok": True, "text": _format_search_results(chunks), "chunks": chunks}


async def execute_search_keyword(
    raw_arguments: str | dict,
    allowed_source_types: list[str] | None = None,
) -> dict[str, Any]:
    """Keyword-only (tsvector FTS) search."""
    from backend.config import DEFAULT_SOURCE_TYPE, KEYWORD_LANGUAGE, RETRIEVAL_MAX_PER_DOCUMENT

    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing required parameter: query"}
    top_k = _clamp_top_k(args.get("top_k"))

    allowed = allowed_source_types or [DEFAULT_SOURCE_TYPE]

    try:
        raw = await repository.keyword_search(
            query,
            top_k=top_k,
            language=KEYWORD_LANGUAGE,
            allowed_source_types=allowed,
        )
        chunks = await _hydrate_chunks(raw)
    except Exception as exc:
        logger.warning("search_keyword failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"search failed: {exc}"}

    chunks = _apply_per_document_cap(chunks, RETRIEVAL_MAX_PER_DOCUMENT)
    chunks = [_normalize_chunk_shape(c) for c in chunks]
    chunks = await _expand_with_neighbors(chunks)
    return {"ok": True, "text": _format_search_results(chunks), "chunks": chunks}


async def execute_search_semantic(
    raw_arguments: str | dict,
    embedding_cache: dict[str, list[float]] | None = None,
    allowed_source_types: list[str] | None = None,
) -> dict[str, Any]:
    """Semantic-only (pgvector cosine) search."""
    from backend.config import DEFAULT_SOURCE_TYPE, RETRIEVAL_MAX_PER_DOCUMENT

    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing required parameter: query"}
    top_k = _clamp_top_k(args.get("top_k"))

    allowed = allowed_source_types or [DEFAULT_SOURCE_TYPE]

    try:
        embedding = await _embed_query(query, embedding_cache)
        raw = await repository.vector_search_pg(
            embedding, top_k=top_k, allowed_source_types=allowed
        )
        chunks = await _hydrate_chunks(raw)
    except Exception as exc:
        logger.warning("search_semantic failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"search failed: {exc}"}

    chunks = _apply_per_document_cap(chunks, RETRIEVAL_MAX_PER_DOCUMENT)
    chunks = [_normalize_chunk_shape(c) for c in chunks]
    chunks = await _expand_with_neighbors(chunks)
    return {"ok": True, "text": _format_search_results(chunks), "chunks": chunks}


async def execute_get_document(
    raw_arguments: str | dict,
    document_id_whitelist: set[str] | None = None,
) -> dict[str, Any]:
    """Full body of one document.

    ``document_id_whitelist`` guards against the model hallucinating ids;
    ``None`` disables the check (tests only).
    """
    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    document_id = args.get("document_id")
    if not isinstance(document_id, str) or not document_id.strip():
        return {"ok": False, "error": "missing required parameter: document_id"}
    document_id = document_id.strip()

    if document_id_whitelist is not None and document_id not in document_id_whitelist:
        return {
            "ok": False,
            "error": (
                f"document_id {document_id!r} is not in the current library. "
                "Only ids from prior search results are valid."
            ),
        }

    try:
        document = await repository.get_document(document_id)
    except Exception as exc:
        logger.warning("get_document: get_document failed for %s: %s", document_id, exc)
        return {"ok": False, "error": f"failed to look up document: {exc}"}
    if not document:
        return {"ok": False, "error": f"document not found: {document_id}"}

    try:
        raw_chunks = await repository.list_chunks_for_document(document_id)
    except Exception as exc:
        logger.warning("get_document: list_chunks_for_document failed for %s: %s", document_id, exc)
        return {"ok": False, "error": f"failed to load chunks: {exc}"}
    if not raw_chunks:
        return {"ok": False, "error": f"no chunks available for document: {document_id}"}

    source_type = document.get("source_type") or "firstspirit"
    chunks = [
        {
            "chunk_id": c.get("id", ""),
            "content": c.get("content", ""),
            "document_id": document_id,
            "document_title": document.get("title", ""),
            "document_url": document.get("url"),
            "document_content_path": document.get("content_path"),
            "source_type": source_type,
            "section_path": c.get("section_path") or [],
            "anchor": c.get("anchor"),
            "chunk_index": c.get("chunk_index", 0),
        }
        for c in raw_chunks
    ]

    from backend.config import DOCUMENT_TOOL_MAX_CHARS

    return {
        "ok": True,
        "text": _format_document(document, raw_chunks, max_chars=DOCUMENT_TOOL_MAX_CHARS),
        "chunks": chunks,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def execute_tool(
    name: str,
    raw_arguments: str | dict,
    document_id_whitelist: set[str] | None = None,
    embedding_cache: dict[str, list[float]] | None = None,
    allowed_source_types: list[str] | None = None,
) -> dict[str, Any]:
    """Dispatch by tool name. Unknown names return an error dict so the model
    sees the refusal and stops calling.

    ``embedding_cache`` is optional per-turn memoisation — if the same query
    text is passed to hybrid and semantic search in one turn, we embed once.

    ``allowed_source_types`` is the retrieval ACL filter, passed through to
    the repository layer. ``None`` defaults to ``[DEFAULT_SOURCE_TYPE]``.
    """
    if name == "search_documents":
        return await execute_search_hybrid(
            raw_arguments,
            embedding_cache=embedding_cache,
            allowed_source_types=allowed_source_types,
        )
    if name == "keyword_search_documents":
        return await execute_search_keyword(
            raw_arguments, allowed_source_types=allowed_source_types
        )
    if name == "semantic_search_documents":
        return await execute_search_semantic(
            raw_arguments,
            embedding_cache=embedding_cache,
            allowed_source_types=allowed_source_types,
        )
    if name == "get_document":
        return await execute_get_document(
            raw_arguments, document_id_whitelist=document_id_whitelist
        )
    return {"ok": False, "error": f"unknown tool: {name}"}


def serialize_tool_result(result: dict[str, Any]) -> str:
    """Convert an executor result into the ``role: tool`` message content."""
    if result.get("ok"):
        return str(result.get("text", ""))
    return f"Error: {result.get('error') or 'tool execution failed'}"

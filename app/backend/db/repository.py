"""
Repository layer — all database access for the FirstSpirit docs RAG.

No raw SQL lives outside this module. Backed by asyncpg via the pool exposed
from ``backend.db.postgres``. The schema is created by Alembic migration
``0001_initial`` — see that file for the table shapes referenced below.

Names mirror the original DynaChat repository where the behavior is identical
(conversations, messages, users-related book-keeping) so the protected
auth/messages modules drop in unchanged. The new vocabulary lives in the
``documents`` and ``document_chunks`` sections.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from backend.db.postgres import get_pg_pool

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    """Aware UTC ``datetime`` — required by asyncpg for TIMESTAMPTZ columns."""
    return datetime.now(UTC)


def _acquire() -> asyncpg.pool.PoolAcquireContext:
    """Pool acquire context. Use as ``async with _acquire() as conn:``."""
    return get_pg_pool().acquire()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def create_document(
    *,
    title: str,
    description: str = "",
    url: str | None = None,
    content_path: str | None = None,
    source_type: str = "firstspirit",
    lang: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Insert a new document row. ``url`` or ``content_path`` must be set."""
    doc_id = _new_id()
    now = _now()
    metadata_json = json.dumps(metadata or {})
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO documents (
                id, title, description, url, content_path, source_type,
                lang, etag, last_modified, content_hash, metadata,
                last_crawled_at, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14)
            """,
            doc_id,
            title,
            description,
            url,
            content_path,
            source_type,
            lang,
            etag,
            last_modified,
            content_hash,
            metadata_json,
            now,
            now,
            now,
        )
    return {
        "id": doc_id,
        "title": title,
        "description": description,
        "url": url,
        "content_path": content_path,
        "source_type": source_type,
        "lang": lang,
        "etag": etag,
        "last_modified": last_modified,
        "content_hash": content_hash,
        "metadata": metadata or {},
        "last_crawled_at": now,
        "created_at": now,
        "updated_at": now,
    }


async def get_document(document_id: str) -> dict | None:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM documents WHERE id = $1", document_id)
    return _hydrate_document(row) if row else None


async def get_document_by_url(url: str) -> dict | None:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM documents WHERE url = $1", url)
    return _hydrate_document(row) if row else None


async def get_document_by_content_path(content_path: str) -> dict | None:
    async with _acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM documents WHERE content_path = $1",
            content_path,
        )
    return _hydrate_document(row) if row else None


async def update_document_crawl_metadata(
    document_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    lang: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Patch crawl-derived metadata after a successful re-fetch.

    Only the columns named in the call are written — None means "leave as is".
    Updates ``last_crawled_at`` and ``updated_at`` to now unconditionally.
    """
    sets: list[str] = []
    params: list[Any] = []
    idx = 1

    def _push(column: str, value: Any) -> None:
        nonlocal idx
        sets.append(f"{column} = ${idx}")
        params.append(value)
        idx += 1

    if title is not None:
        _push("title", title)
    if description is not None:
        _push("description", description)
    if lang is not None:
        _push("lang", lang)
    if etag is not None:
        _push("etag", etag)
    if last_modified is not None:
        _push("last_modified", last_modified)
    if content_hash is not None:
        _push("content_hash", content_hash)
    if metadata is not None:
        sets.append(f"metadata = ${idx}::jsonb")
        params.append(json.dumps(metadata))
        idx += 1

    now = _now()
    sets.append(f"last_crawled_at = ${idx}")
    params.append(now)
    idx += 1
    sets.append(f"updated_at = ${idx}")
    params.append(now)
    idx += 1

    params.append(document_id)
    sql = f"UPDATE documents SET {', '.join(sets)} WHERE id = ${idx}"
    async with _acquire() as conn:
        result: str = await conn.execute(sql, *params)
    return result != "UPDATE 0"


async def list_documents() -> list[dict]:
    """List documents for the catalog and ``/api/documents``."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, description, url, content_path, source_type,
                   lang, last_crawled_at, created_at
            FROM documents
            ORDER BY created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def list_documents_admin() -> list[dict]:
    """Documents with chunk_count, newest first."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.title, d.description, d.url, d.content_path,
                   d.source_type, d.lang, d.last_crawled_at, d.created_at,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id)
                       AS chunk_count
            FROM documents d
            ORDER BY d.created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def search_documents_admin(q: str, limit: int = 20) -> list[dict]:
    pattern = f"%{q}%"
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.title, d.description, d.url, d.content_path,
                   d.source_type, d.lang, d.last_crawled_at, d.created_at,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id)
                       AS chunk_count
            FROM documents d
            WHERE d.title ILIKE $1 OR d.description ILIKE $1 OR d.url ILIKE $1
            ORDER BY d.created_at DESC
            LIMIT $2
            """,
            pattern,
            limit,
        )
    return [dict(r) for r in rows]


async def delete_document_cascade(document_id: str) -> bool:
    async with _acquire() as conn:
        result: str = await conn.execute("DELETE FROM documents WHERE id = $1", document_id)
        return result != "DELETE 0"


async def count_documents() -> int:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) FROM documents")
    return row[0] if row else 0


def _hydrate_document(row: asyncpg.Record) -> dict:
    """Convert an asyncpg Record to a dict; deserialise ``metadata`` JSON."""
    d = dict(row)
    meta = d.get("metadata")
    if isinstance(meta, str):
        d["metadata"] = json.loads(meta) if meta else {}
    return d


# ---------------------------------------------------------------------------
# Document chunks
# ---------------------------------------------------------------------------


async def list_chunks_for_document(document_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, document_id, content, embedding, chunk_index,
                   section_path, anchor, char_start, char_end, source_type
            FROM document_chunks
            WHERE document_id = $1
            ORDER BY chunk_index
            """,
            document_id,
        )
    result: list[dict] = []
    for r in rows:
        d = dict(r)
        d["embedding"] = json.loads(d["embedding"])
        d["section_path"] = (
            json.loads(d["section_path"])
            if isinstance(d["section_path"], str)
            else d["section_path"]
        )
        result.append(d)
    return result


async def get_chunk_neighbors(
    document_id: str,
    chunk_index: int,
    window: int = 1,
) -> list[dict]:
    """Return chunks within ``[chunk_index ± window]`` for a document.

    Used by the expansion step to fetch surrounding context for a hit. Joins
    ``documents`` so the merged-span citation can include ``document_title`` /
    ``document_url`` without a second round-trip.
    """
    min_index = max(0, chunk_index - window)
    max_index = chunk_index + window
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   c.section_path, c.anchor, c.char_start, c.char_end,
                   d.title AS document_title, d.url AS document_url,
                   d.content_path AS document_content_path,
                   d.source_type AS document_source_type
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.document_id = $1 AND c.chunk_index >= $2 AND c.chunk_index <= $3
            ORDER BY c.chunk_index ASC
            """,
            document_id,
            min_index,
            max_index,
        )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("section_path"), str):
            d["section_path"] = json.loads(d["section_path"])
        out.append(d)
    return out


async def count_chunks() -> int:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) FROM document_chunks")
    return row[0] if row else 0


async def replace_chunks_for_document(
    document_id: str,
    chunks: list[dict],
    *,
    source_type: str = "firstspirit",
) -> None:
    """Atomically replace all chunks for *document_id*.

    Each entry in *chunks* must have keys: ``content``, ``embedding``,
    ``chunk_index``, ``section_path``, ``anchor``, ``char_start``, ``char_end``.
    Caller must finish chunking + embedding BEFORE invoking so a crawler or
    OpenRouter failure cannot leave the document chunkless.
    """
    async with _acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM document_chunks WHERE document_id = $1", document_id)
        for c in chunks:
            await conn.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, content, embedding, chunk_index,
                    section_path, anchor, char_start, char_end, source_type
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10)
                """,
                _new_id(),
                document_id,
                c["content"],
                json.dumps(c["embedding"]),
                c["chunk_index"],
                json.dumps(c.get("section_path", [])),
                c.get("anchor"),
                c.get("char_start", 0),
                c.get("char_end", 0),
                source_type,
            )


# ---------------------------------------------------------------------------
# Hybrid retrieval helpers (tsvector + pgvector via RRF)
# ---------------------------------------------------------------------------


async def keyword_search(
    query: str,
    top_k: int,
    language: str = "english",
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Top-K chunks matching a full-text query.

    Returns rows with keys: ``id``, ``document_id``, ``content``,
    ``chunk_index``, ``section_path``, ``anchor``, ``rank``. ``rank`` is the
    ``ts_rank`` score the RRF merger uses for ordering.
    """
    if allowed_source_types is None:
        allowed_source_types = ["firstspirit"]
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, document_id, content, chunk_index,
                   section_path, anchor,
                   ts_rank(search_vector, plainto_tsquery($4, $1)) AS rank
            FROM document_chunks
            WHERE search_vector @@ plainto_tsquery($4, $1)
              AND source_type = ANY($3::text[])
            ORDER BY rank DESC
            LIMIT $2
            """,
            query,
            top_k,
            allowed_source_types,
            language,
        )
    return [_hydrate_chunk(r) for r in rows]


async def vector_search_pg(
    query_embedding: list[float],
    top_k: int,
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Top-K chunks by pgvector cosine similarity.

    The ``embedding`` column is stored as TEXT (JSON-encoded). The query
    casts both sides via ``::vector`` so pgvector's ``<=>`` operator can
    compute cosine distance.
    """
    if allowed_source_types is None:
        allowed_source_types = ["firstspirit"]
    embedding_json = json.dumps(query_embedding)
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, document_id, content, chunk_index,
                   section_path, anchor,
                   embedding::vector <=> $1::vector AS distance
            FROM document_chunks
            WHERE source_type = ANY($3::text[])
            ORDER BY distance
            LIMIT $2
            """,
            embedding_json,
            top_k,
            allowed_source_types,
        )
    return [_hydrate_chunk(r) for r in rows]


def _hydrate_chunk(row: asyncpg.Record) -> dict:
    d = dict(row)
    if isinstance(d.get("section_path"), str):
        d["section_path"] = json.loads(d["section_path"])
    return d


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


async def create_conversation(*, user_id: str, title: str = "New Conversation") -> dict:
    conv_id = _new_id()
    now = _now()
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO conversations (id, user_id, title, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            conv_id,
            user_id,
            title,
            now,
            now,
        )
    return {
        "id": conv_id,
        "user_id": user_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }


async def get_conversation(conv_id: str, user_id: str) -> dict | None:
    """Return the conversation only if it belongs to the given user."""
    async with _acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM conversations WHERE id = $1 AND user_id = $2",
            conv_id,
            user_id,
        )
    return dict(row) if row else None


async def list_conversations(user_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.*,
                   (SELECT content
                    FROM messages
                    WHERE conversation_id = c.id
                    ORDER BY created_at DESC
                    LIMIT 1) AS preview
            FROM conversations c
            WHERE c.user_id = $1
            ORDER BY c.updated_at DESC
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def update_conversation_title(conv_id: str, user_id: str, title: str) -> bool:
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE conversations SET title = $1, updated_at = $2 WHERE id = $3 AND user_id = $4",
            title,
            _now(),
            conv_id,
            user_id,
        )
        return result != "UPDATE 0"


async def touch_conversation(conv_id: str, user_id: str) -> None:
    async with _acquire() as conn:
        await conn.execute(
            "UPDATE conversations SET updated_at = $1 WHERE id = $2 AND user_id = $3",
            _now(),
            conv_id,
            user_id,
        )


async def delete_conversation(conv_id: str, user_id: str) -> bool:
    async with _acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversations WHERE id = $1 AND user_id = $2",
            conv_id,
            user_id,
        )
        return result != "DELETE 0"


async def search_conversations_by_title(user_id: str, query: str, limit: int = 20) -> list[dict]:
    pattern = f"%{query}%"
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = $1 AND title ILIKE $2
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            pattern,
            limit,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def create_message(
    *,
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
) -> dict | None:
    """Insert a message. Returns None if the conversation does not belong to *user_id*.

    The INSERT is guarded by an EXISTS subquery so ownership is enforced at
    the SQL layer even if a caller forgets to check.
    """
    msg_id = _new_id()
    now = _now()
    sources_json = json.dumps(sources) if sources is not None else None
    async with _acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, sources, created_at)
            SELECT $1, $2, $3, $4, $5::jsonb, $6
            WHERE EXISTS (
                SELECT 1 FROM conversations WHERE id = $7 AND user_id = $8
            )
            """,
            msg_id,
            conversation_id,
            role,
            content,
            sources_json,
            now,
            conversation_id,
            user_id,
        )
        if result == "INSERT 0 0":
            return None
    await touch_conversation(conversation_id, user_id)
    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "sources": sources,
        "created_at": now,
    }


async def list_messages(conversation_id: str, user_id: str) -> list[dict]:
    """Return messages only if the conversation belongs to *user_id*."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.*
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = $1 AND c.user_id = $2
            ORDER BY m.created_at ASC
            """,
            conversation_id,
            user_id,
        )
    results: list[dict] = []
    for r in rows:
        d = dict(r)
        # asyncpg returns JSONB as a raw string when no type codec is registered.
        d["sources"] = json.loads(d["sources"]) if d.get("sources") else None
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Source sync runs (generic — covers url_list + vault)
# ---------------------------------------------------------------------------


async def create_sync_run(
    *,
    sync_run_id: str,
    kind: str,
    started_at: datetime | str,
) -> dict:
    """Create a new sync-run audit row.

    ``kind`` must be one of: ``"url_list"``, ``"vault"`` (CHECK constraint
    enforces this at the SQL layer).
    """
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO source_sync_runs (
                id, kind, status,
                items_total, items_new, items_updated, items_unchanged, items_error,
                started_at
            )
            VALUES ($1, $2, 'running', 0, 0, 0, 0, 0, $3)
            """,
            sync_run_id,
            kind,
            started_at,
        )
    started_at_str = started_at.isoformat() if isinstance(started_at, datetime) else started_at
    return {
        "id": sync_run_id,
        "kind": kind,
        "status": "running",
        "items_total": 0,
        "items_new": 0,
        "items_updated": 0,
        "items_unchanged": 0,
        "items_error": 0,
        "started_at": started_at_str,
        "finished_at": None,
    }


async def update_sync_run(
    *,
    sync_run_id: str,
    status: str,
    finished_at: datetime | str | None = None,
    items_total: int = 0,
    items_new: int = 0,
    items_updated: int = 0,
    items_unchanged: int = 0,
    items_error: int = 0,
) -> bool:
    async with _acquire() as conn:
        result: str = await conn.execute(
            """
            UPDATE source_sync_runs
            SET status = $1, finished_at = $2,
                items_total = $3, items_new = $4, items_updated = $5,
                items_unchanged = $6, items_error = $7
            WHERE id = $8
            """,
            status,
            finished_at,
            items_total,
            items_new,
            items_updated,
            items_unchanged,
            items_error,
            sync_run_id,
        )
        return result != "UPDATE 0"


async def list_sync_runs(limit: int = 10) -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM source_sync_runs
            ORDER BY started_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Source sync items (per URL / per vault file)
# ---------------------------------------------------------------------------


async def create_sync_item(
    *,
    sync_run_id: str,
    source_ref: str,
    outcome: str = "pending",
) -> dict:
    item_id = _new_id()
    now = _now()
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO source_sync_items (id, sync_run_id, source_ref, outcome, created_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            item_id,
            sync_run_id,
            source_ref,
            outcome,
            now,
        )
    return {
        "id": item_id,
        "sync_run_id": sync_run_id,
        "source_ref": source_ref,
        "outcome": outcome,
        "error_message": None,
        "created_at": now,
    }


async def update_sync_item_outcome(
    item_id: str,
    outcome: str,
    error_message: str | None = None,
) -> bool:
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE source_sync_items SET outcome = $1, error_message = $2 WHERE id = $3",
            outcome,
            error_message,
            item_id,
        )
        return result != "UPDATE 0"


async def list_sync_items_for_run(sync_run_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM source_sync_items WHERE sync_run_id = $1 ORDER BY created_at",
            sync_run_id,
        )
    return [dict(r) for r in rows]

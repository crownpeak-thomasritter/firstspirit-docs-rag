"""
Repository layer — all SQLite access for the FirstSpirit docs RAG.

No raw SQL lives outside this module. Backed by ``aiosqlite`` via the helper
exposed from ``backend.db.sqlite``. The schema is created by Alembic
migration ``0001_initial`` — see that file for the table shapes.

Vector storage (dense + sparse) and hybrid retrieval live in
``backend.rag.vector_store`` against Qdrant; this module no longer carries
any vector or full-text-search code.

The ``_acquire()`` context manager auto-commits on clean exit and rolls back
on exception, so individual functions don't have to remember
``await conn.commit()`` after every INSERT/UPDATE/DELETE.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.db.sqlite import _acquire

logger = logging.getLogger(__name__)

__all__ = [
    "_acquire",
    "count_chunks",
    "count_documents",
    "create_conversation",
    "create_document",
    "create_message",
    "create_sync_item",
    "create_sync_run",
    "delete_conversation",
    "delete_document_cascade",
    "get_chunk_neighbors",
    "get_conversation",
    "get_document",
    "get_document_by_content_path",
    "get_document_by_url",
    "list_chunks_for_document",
    "list_conversations",
    "list_documents",
    "list_documents_admin",
    "list_messages",
    "list_sync_items_for_run",
    "list_sync_runs",
    "replace_chunks_for_document",
    "search_conversations_by_title",
    "search_documents_admin",
    "touch_conversation",
    "update_conversation_title",
    "update_document_crawl_metadata",
    "update_sync_item_outcome",
    "update_sync_run",
]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    """ISO-8601 UTC timestamp — the wire format SQLite stores in TEXT columns."""
    return datetime.now(UTC).isoformat()


async def _fetchall(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> list[dict]:
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _fetchone(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> dict | None:
    async with conn.execute(sql, params) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _execute(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> int:
    """Execute a write and return ``cur.rowcount`` (used to detect 0-row updates)."""
    async with conn.execute(sql, params) as cur:
        return cur.rowcount


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
        await _execute(
            conn,
            """
            INSERT INTO documents (
                id, title, description, url, content_path, source_type,
                lang, etag, last_modified, content_hash, metadata,
                last_crawled_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
            ),
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
        row = await _fetchone(conn, "SELECT * FROM documents WHERE id = ?", (document_id,))
    return _hydrate_document(row) if row else None


async def get_document_by_url(url: str) -> dict | None:
    async with _acquire() as conn:
        row = await _fetchone(conn, "SELECT * FROM documents WHERE url = ?", (url,))
    return _hydrate_document(row) if row else None


async def get_document_by_content_path(content_path: str) -> dict | None:
    async with _acquire() as conn:
        row = await _fetchone(
            conn,
            "SELECT * FROM documents WHERE content_path = ?",
            (content_path,),
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

    Only the columns named in the call are written — ``None`` means "leave
    as is". Updates ``last_crawled_at`` and ``updated_at`` to now unconditionally.
    """
    sets: list[str] = []
    params: list[Any] = []

    def _push(column: str, value: Any) -> None:
        sets.append(f"{column} = ?")
        params.append(value)

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
        _push("metadata", json.dumps(metadata))

    now = _now()
    _push("last_crawled_at", now)
    _push("updated_at", now)
    params.append(document_id)
    sql = f"UPDATE documents SET {', '.join(sets)} WHERE id = ?"
    async with _acquire() as conn:
        rowcount = await _execute(conn, sql, tuple(params))
    return rowcount > 0


async def list_documents() -> list[dict]:
    """List documents for the catalog and ``/api/documents``."""
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT id, title, description, url, content_path, source_type,
                   lang, last_crawled_at, created_at
            FROM documents
            ORDER BY created_at DESC
            """,
        )
    return rows


async def list_documents_admin() -> list[dict]:
    """Documents with chunk_count, newest first."""
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT d.id, d.title, d.description, d.url, d.content_path,
                   d.source_type, d.lang, d.last_crawled_at, d.created_at,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id)
                       AS chunk_count
            FROM documents d
            ORDER BY d.created_at DESC
            """,
        )
    return rows


async def search_documents_admin(q: str, limit: int = 20) -> list[dict]:
    pattern = f"%{q}%"
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT d.id, d.title, d.description, d.url, d.content_path,
                   d.source_type, d.lang, d.last_crawled_at, d.created_at,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id)
                       AS chunk_count
            FROM documents d
            WHERE d.title LIKE ? OR d.description LIKE ? OR d.url LIKE ?
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        )
    return rows


async def delete_document_cascade(document_id: str) -> bool:
    async with _acquire() as conn:
        rowcount = await _execute(conn, "DELETE FROM documents WHERE id = ?", (document_id,))
    return rowcount > 0


async def count_documents() -> int:
    async with _acquire() as conn:
        row = await _fetchone(conn, "SELECT COUNT(*) AS n FROM documents")
    return int(row["n"]) if row else 0


def _hydrate_document(row: dict) -> dict:
    """Deserialise the ``metadata`` JSON column. ``row`` is already a dict."""
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
        rows = await _fetchall(
            conn,
            """
            SELECT id, document_id, content, chunk_index,
                   section_path, anchor, char_start, char_end, source_type
            FROM document_chunks
            WHERE document_id = ?
            ORDER BY chunk_index
            """,
            (document_id,),
        )
    for d in rows:
        if isinstance(d.get("section_path"), str):
            d["section_path"] = json.loads(d["section_path"]) if d["section_path"] else []
    return rows


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
        rows = await _fetchall(
            conn,
            """
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   c.section_path, c.anchor, c.char_start, c.char_end,
                   d.title AS document_title, d.url AS document_url,
                   d.content_path AS document_content_path,
                   d.source_type AS document_source_type
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.document_id = ? AND c.chunk_index >= ? AND c.chunk_index <= ?
            ORDER BY c.chunk_index ASC
            """,
            (document_id, min_index, max_index),
        )
    for d in rows:
        if isinstance(d.get("section_path"), str):
            d["section_path"] = json.loads(d["section_path"]) if d["section_path"] else []
    return rows


async def count_chunks() -> int:
    async with _acquire() as conn:
        row = await _fetchone(conn, "SELECT COUNT(*) AS n FROM document_chunks")
    return int(row["n"]) if row else 0


async def replace_chunks_for_document(
    document_id: str,
    chunks: list[dict],
    *,
    source_type: str = "firstspirit",
) -> None:
    """Atomically replace all chunks for *document_id*.

    Each entry in *chunks* must have keys: ``content``, ``chunk_index``,
    ``section_path``, ``anchor``, ``char_start``, ``char_end``. If a chunk
    dict carries ``chunk_id``, that value is used as the row primary key so
    the same id can be reused on the Qdrant side; otherwise a fresh UUID is
    generated. Caller must finish chunking BEFORE invoking so a crawler or
    embedder failure cannot leave the document chunkless.

    The vectors themselves live in Qdrant — see
    ``rag.vector_store.upsert_chunks`` for the parallel write.
    """
    async with _acquire() as conn:
        await _execute(
            conn,
            "DELETE FROM document_chunks WHERE document_id = ?",
            (document_id,),
        )
        for c in chunks:
            chunk_id = c.get("chunk_id") or _new_id()
            await _execute(
                conn,
                """
                INSERT INTO document_chunks (
                    id, document_id, content, chunk_index,
                    section_path, anchor, char_start, char_end, source_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    document_id,
                    c["content"],
                    c["chunk_index"],
                    json.dumps(c.get("section_path", [])),
                    c.get("anchor"),
                    c.get("char_start", 0),
                    c.get("char_end", 0),
                    source_type,
                ),
            )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


async def create_conversation(*, user_id: str, title: str = "New Conversation") -> dict:
    conv_id = _new_id()
    now = _now()
    async with _acquire() as conn:
        await _execute(
            conn,
            """
            INSERT INTO conversations (id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, user_id, title, now, now),
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
        row = await _fetchone(
            conn,
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
    return row


async def list_conversations(user_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT c.*,
                   (SELECT content
                    FROM messages
                    WHERE conversation_id = c.id
                    ORDER BY created_at DESC
                    LIMIT 1) AS preview
            FROM conversations c
            WHERE c.user_id = ?
            ORDER BY c.updated_at DESC
            """,
            (user_id,),
        )
    return rows


async def update_conversation_title(conv_id: str, user_id: str, title: str) -> bool:
    async with _acquire() as conn:
        rowcount = await _execute(
            conn,
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, _now(), conv_id, user_id),
        )
    return rowcount > 0


async def touch_conversation(conv_id: str, user_id: str) -> None:
    async with _acquire() as conn:
        await _execute(
            conn,
            "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
            (_now(), conv_id, user_id),
        )


async def delete_conversation(conv_id: str, user_id: str) -> bool:
    async with _acquire() as conn:
        rowcount = await _execute(
            conn,
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
    return rowcount > 0


async def search_conversations_by_title(user_id: str, query: str, limit: int = 20) -> list[dict]:
    pattern = f"%{query}%"
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = ? AND title LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, pattern, limit),
        )
    return rows


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
        async with conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, sources, created_at)
            SELECT ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM conversations WHERE id = ? AND user_id = ?
            )
            """,
            (
                msg_id,
                conversation_id,
                role,
                content,
                sources_json,
                now,
                conversation_id,
                user_id,
            ),
        ) as cur:
            inserted = cur.rowcount
        if inserted == 0:
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
        rows = await _fetchall(
            conn,
            """
            SELECT m.*
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = ? AND c.user_id = ?
            ORDER BY m.created_at ASC
            """,
            (conversation_id, user_id),
        )
    for d in rows:
        d["sources"] = json.loads(d["sources"]) if d.get("sources") else None
    return rows


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
    started_at_str = started_at.isoformat() if isinstance(started_at, datetime) else started_at
    async with _acquire() as conn:
        await _execute(
            conn,
            """
            INSERT INTO source_sync_runs (
                id, kind, status,
                items_total, items_new, items_updated, items_unchanged, items_error,
                started_at
            )
            VALUES (?, ?, 'running', 0, 0, 0, 0, 0, ?)
            """,
            (sync_run_id, kind, started_at_str),
        )
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
    finished_at_str: str | None = (
        finished_at.isoformat() if isinstance(finished_at, datetime) else finished_at
    )
    async with _acquire() as conn:
        rowcount = await _execute(
            conn,
            """
            UPDATE source_sync_runs
            SET status = ?, finished_at = ?,
                items_total = ?, items_new = ?, items_updated = ?,
                items_unchanged = ?, items_error = ?
            WHERE id = ?
            """,
            (
                status,
                finished_at_str,
                items_total,
                items_new,
                items_updated,
                items_unchanged,
                items_error,
                sync_run_id,
            ),
        )
    return rowcount > 0


async def list_sync_runs(limit: int = 10) -> list[dict]:
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            """
            SELECT * FROM source_sync_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    return rows


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
        await _execute(
            conn,
            """
            INSERT INTO source_sync_items (id, sync_run_id, source_ref, outcome, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, sync_run_id, source_ref, outcome, now),
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
        rowcount = await _execute(
            conn,
            "UPDATE source_sync_items SET outcome = ?, error_message = ? WHERE id = ?",
            (outcome, error_message, item_id),
        )
    return rowcount > 0


async def list_sync_items_for_run(sync_run_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await _fetchall(
            conn,
            "SELECT * FROM source_sync_items WHERE sync_run_id = ? ORDER BY created_at",
            (sync_run_id,),
        )
    return rows

"""Smoke tests for the repository → SQLite port.

Drives the rewritten ``backend.db.repository`` against an in-memory SQLite
database, applying the Alembic migration first so the schema matches what
the runtime app would see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db import repository
from backend.db import sqlite as sqlite_mod


async def _apply_initial_schema() -> None:
    """Apply the schema by executing the SQL inside the Alembic upgrade()."""
    import aiosqlite

    async with aiosqlite.connect(sqlite_mod.get_db_path()) as conn:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                url TEXT,
                content_path TEXT,
                source_type TEXT NOT NULL DEFAULT 'firstspirit',
                lang TEXT,
                etag TEXT,
                last_modified TEXT,
                content_hash TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                last_crawled_at TEXT,
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
            );
            CREATE TABLE IF NOT EXISTS document_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                section_path TEXT NOT NULL DEFAULT '[]',
                anchor TEXT,
                char_start INTEGER NOT NULL DEFAULT 0,
                char_end INTEGER NOT NULL DEFAULT 0,
                source_type TEXT NOT NULL DEFAULT 'firstspirit'
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Conversation',
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                sources TEXT,
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
            );
            CREATE TABLE IF NOT EXISTS source_sync_runs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('url_list', 'vault')),
                status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                items_total INTEGER NOT NULL DEFAULT 0,
                items_new INTEGER NOT NULL DEFAULT 0,
                items_updated INTEGER NOT NULL DEFAULT 0,
                items_unchanged INTEGER NOT NULL DEFAULT 0,
                items_error INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS source_sync_items (
                id TEXT PRIMARY KEY,
                sync_run_id TEXT NOT NULL REFERENCES source_sync_runs(id) ON DELETE CASCADE,
                source_ref TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN (
                    'pending', 'ingested', 'updated', 'unchanged', 'error'
                )),
                error_message TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        await conn.commit()


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path: Path):
    """Per-test on-disk SQLite — in-memory connections aren't shared across
    aiosqlite open/close, so an on-disk file is the simplest reliable store.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(sqlite_mod, "_db_path", None, raising=False)
    monkeypatch.setattr(
        "backend.config.DATABASE_URL",
        f"sqlite+aiosqlite:///{db_path}",
        raising=False,
    )
    # The sqlite module reads DATABASE_URL at init time via its own import.
    import importlib

    importlib.reload(sqlite_mod)
    # repository imports _acquire from sqlite_mod by name at import time, so
    # reload it too to pick up the freshly-bound function.
    importlib.reload(repository)
    await sqlite_mod.init_sqlite_db()
    await _apply_initial_schema()
    yield
    await sqlite_mod.close_sqlite_db()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def test_create_and_get_document_roundtrip(fresh_db):
    doc = await repository.create_document(
        title="T",
        description="D",
        url="https://docs.example/x",
        metadata={"k": "v"},
    )
    got = await repository.get_document(doc["id"])
    assert got is not None
    assert got["title"] == "T"
    assert got["url"] == "https://docs.example/x"
    assert got["metadata"] == {"k": "v"}


async def test_count_documents_reflects_inserts(fresh_db):
    assert await repository.count_documents() == 0
    await repository.create_document(title="A", url="https://docs.example/a")
    await repository.create_document(title="B", url="https://docs.example/b")
    assert await repository.count_documents() == 2


async def test_delete_document_cascades_chunks(fresh_db):
    doc = await repository.create_document(title="X", url="https://docs.example/x")
    await repository.replace_chunks_for_document(
        doc["id"],
        [
            {
                "chunk_id": "ck-1",
                "content": "Hello",
                "chunk_index": 0,
                "section_path": ["Top"],
                "anchor": "top",
                "char_start": 0,
                "char_end": 5,
            }
        ],
    )
    assert await repository.count_chunks() == 1
    await repository.delete_document_cascade(doc["id"])
    assert await repository.count_chunks() == 0


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


async def test_replace_chunks_preserves_supplied_chunk_id(fresh_db):
    doc = await repository.create_document(title="X", url="https://docs.example/x")
    await repository.replace_chunks_for_document(
        doc["id"],
        [
            {
                "chunk_id": "stable-id-42",
                "content": "Body",
                "chunk_index": 0,
                "section_path": [],
                "anchor": None,
                "char_start": 0,
                "char_end": 4,
            }
        ],
    )
    chunks = await repository.list_chunks_for_document(doc["id"])
    assert chunks[0]["id"] == "stable-id-42"


async def test_get_chunk_neighbors_window(fresh_db):
    doc = await repository.create_document(title="X", url="https://docs.example/x")
    payload = [
        {
            "chunk_id": f"c{i}",
            "content": f"chunk {i}",
            "chunk_index": i,
            "section_path": [],
            "anchor": None,
            "char_start": i * 10,
            "char_end": i * 10 + 9,
        }
        for i in range(5)
    ]
    await repository.replace_chunks_for_document(doc["id"], payload)
    neighbours = await repository.get_chunk_neighbors(doc["id"], chunk_index=2, window=1)
    indexes = sorted(c["chunk_index"] for c in neighbours)
    assert indexes == [1, 2, 3]


# ---------------------------------------------------------------------------
# Conversations + messages cascade
# ---------------------------------------------------------------------------


async def test_create_message_and_cascade(fresh_db):
    conv = await repository.create_conversation(user_id="u1", title="C")
    msg = await repository.create_message(
        conversation_id=conv["id"],
        user_id="u1",
        role="user",
        content="hello",
    )
    assert msg is not None
    listed = await repository.list_messages(conv["id"], "u1")
    assert len(listed) == 1
    assert listed[0]["content"] == "hello"

    deleted = await repository.delete_conversation(conv["id"], "u1")
    assert deleted is True
    after = await repository.list_messages(conv["id"], "u1")
    assert after == []


async def test_create_message_returns_none_when_conversation_missing(fresh_db):
    msg = await repository.create_message(
        conversation_id="does-not-exist",
        user_id="u1",
        role="user",
        content="hi",
    )
    assert msg is None


async def test_search_documents_admin_uses_like(fresh_db):
    await repository.create_document(title="ODFS Reference", url="https://docs.example/odfs")
    await repository.create_document(title="Other", url="https://docs.example/other")
    rows = await repository.search_documents_admin("odfs")
    assert len(rows) == 1
    assert rows[0]["title"] == "ODFS Reference"

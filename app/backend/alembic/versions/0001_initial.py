"""Initial schema — FirstSpirit documentation RAG (SQLite).

Fresh baseline for the pivoted project. SQLite-compatible DDL only — no
Postgres extensions, no pgvector, no tsvector, no auth tables.

* ``documents``: documentation pages identified by URL or vault content path,
  with crawler conditional-GET state (etag, last_modified) and an idempotency
  hash for vault re-ingestion. ``metadata`` is JSON-encoded TEXT.
* ``document_chunks``: chunks of the document body, carrying the heading
  breadcrumb (``section_path``) and a deep-link anchor. Vector storage and
  hybrid retrieval live in Qdrant (``rag/vector_store.py``); this table only
  carries the text + structural metadata SQLite-side.
* ``source_sync_runs`` / ``source_sync_items``: generic ingest-run audit.
  ``source_ref`` is a URL for crawled sources and a vault relative path for
  markdown sources.
* ``conversations`` / ``messages``: chat persistence. ``sources`` is a
  JSON-encoded TEXT column.

Timestamps are ISO-8601 TEXT (the application formats with ``datetime.isoformat``).

Revision ID: 0001
Revises:
Create Date: 2026-05-13

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- documents -------------------------------------------------------
    # `url` is the canonical source URL for crawled docs; `content_path` is
    # the vault-relative path for markdown sources. Either one is present
    # depending on `source_type`. `etag`/`last_modified` are stored verbatim
    # for the next conditional GET. `content_hash` is the SHA-256 of the raw
    # body — used by the vault ingester to skip unchanged files cheaply.
    op.execute(
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
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS documents_url_uidx "
        "ON documents (url) WHERE url IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS documents_content_path_uidx "
        "ON documents (content_path) WHERE content_path IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS documents_source_type_idx ON documents (source_type)")

    # --- document_chunks -------------------------------------------------
    # Vectors live in Qdrant; this table holds only text and structural
    # metadata used for citation rendering, expansion (neighbor chunks),
    # and the `get_document` LLM tool. `section_path` is a JSON-encoded
    # TEXT list of headings ["Top", "Sub"] preserving the breadcrumb.
    # `anchor` is the slug of the deepest heading — citations link to
    # `url#anchor`.
    op.execute(
        """
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
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx "
        "ON document_chunks (document_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS document_chunks_source_type_idx "
        "ON document_chunks (source_type)"
    )

    # --- conversations ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New Conversation',
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS conversations_user_id_idx ON conversations (user_id)")

    # --- messages --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            sources TEXT,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        )
        """
    )

    # --- source_sync_runs ------------------------------------------------
    # Generic ingest-run audit. `kind` discriminates the pipeline type:
    # 'url_list' for the crawled URL List, 'vault' for the markdown vault.
    op.execute(
        """
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
        )
        """
    )

    # --- source_sync_items -----------------------------------------------
    # One row per URL/path attempted within a run. `source_ref` is the URL
    # (for url_list) or the vault-relative path (for vault). `outcome` adds
    # 'unchanged' so the run summary can distinguish "fetched and
    # re-embedded" from "skipped by If-None-Match / content_hash match".
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_sync_items (
            id TEXT PRIMARY KEY,
            sync_run_id TEXT NOT NULL REFERENCES source_sync_runs(id) ON DELETE CASCADE,
            source_ref TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN (
                'pending', 'ingested', 'updated', 'unchanged', 'error'
            )),
            error_message TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS source_sync_items_sync_run_id_idx "
        "ON source_sync_items (sync_run_id)"
    )


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.execute("DROP TABLE IF EXISTS source_sync_items")
    op.execute("DROP TABLE IF EXISTS source_sync_runs")
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversations")
    op.execute("DROP TABLE IF EXISTS document_chunks")
    op.execute("DROP TABLE IF EXISTS documents")

"""Initial schema — FirstSpirit documentation RAG.

Fresh baseline for the pivoted project. Creates the target shape directly:

* ``documents`` (replaces ``videos``): documentation pages identified by URL or
  vault content path, with crawler conditional-GET state (etag, last_modified)
  and an idempotency hash for vault re-ingestion.
* ``document_chunks`` (replaces ``chunks``): chunks of the document body,
  carrying the heading breadcrumb (``section_path``) and a deep-link anchor
  instead of YouTube timestamps. Includes the tsvector ``search_vector``
  GENERATED column + GIN index needed by hybrid retrieval.
* ``source_sync_runs`` / ``source_sync_items`` (replace the ``channel_sync_*``
  pair): generic ingest-run audit. ``source_ref`` is a URL for crawled sources
  and a vault relative path for markdown sources.
* Auth tables (``users``, ``user_messages``, ``signup_attempts``) and chat
  tables (``conversations``, ``messages``) — shape-compatible with the donor
  so the protected auth / messages modules drop in cleanly when added.

Revision ID: 0001
Revises:
Create Date: 2026-05-12

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- Extensions -------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # pgvector is required for vector_search_pg's `embedding::vector` cast.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- users -----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email CITEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ,
            daily_message_count INTEGER NOT NULL DEFAULT 0,
            rate_window_start TIMESTAMPTZ,
            is_member BOOLEAN NOT NULL DEFAULT FALSE,
            member_verified_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS users_email_idx ON users (email)")

    # --- user_messages (sliding-window audit for 25 msg/user/24h cap) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS user_messages_user_id_created_at_idx "
        "ON user_messages (user_id, created_at DESC)"
    )

    # --- signup_attempts (audit trail for signup rate-limiting) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS signup_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ip INET NOT NULL,
            email_attempted CITEXT,
            outcome TEXT NOT NULL CHECK (outcome IN (
                'accepted','ip_limited','global_limited','duplicate','invalid'
            )),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS signup_attempts_ip_created_at_idx "
        "ON signup_attempts (ip, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS signup_attempts_created_at_idx "
        "ON signup_attempts (created_at DESC)"
    )

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
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_crawled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
    # `embedding` is JSON-encoded text (matches the donor pattern); the
    # retriever casts it to `vector` at query time. `section_path` is a
    # JSONB list of headings ["Top", "Sub"] preserving the breadcrumb.
    # `anchor` is the slug of the deepest heading — citations link to
    # `url#anchor`. `search_vector` is GENERATED so chunk inserts do not
    # need to maintain it.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            embedding TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            section_path JSONB NOT NULL DEFAULT '[]'::jsonb,
            anchor TEXT,
            char_start INTEGER NOT NULL DEFAULT 0,
            char_end INTEGER NOT NULL DEFAULT 0,
            source_type TEXT NOT NULL DEFAULT 'firstspirit',
            search_vector tsvector GENERATED ALWAYS AS (
                to_tsvector('english', content)
            ) STORED
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
    op.execute(
        "CREATE INDEX IF NOT EXISTS document_chunks_search_vector_idx "
        "ON document_chunks USING GIN(search_vector)"
    )

    # --- conversations ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New Conversation',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
            sources JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ
        )
        """
    )

    # --- source_sync_items -----------------------------------------------
    # One row per URL/path attempted within a run. `source_ref` is the URL
    # (for url_list) or the vault-relative path (for vault). `outcome`
    # mirrors `status` from the donor but adds 'unchanged' so the run
    # summary can distinguish "fetched and re-embedded" from "skipped by
    # If-None-Match / content_hash match".
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
            created_at TIMESTAMPTZ NOT NULL
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
    op.execute("DROP TABLE IF EXISTS signup_attempts")
    op.execute("DROP TABLE IF EXISTS user_messages")
    op.execute("DROP TABLE IF EXISTS users")
    # Extensions left in place — see donor migration for rationale.

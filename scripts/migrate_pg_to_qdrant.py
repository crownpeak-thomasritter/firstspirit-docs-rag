"""One-shot migration: Postgres+pgvector → SQLite + Qdrant Cloud.

Reads all rows from ``documents`` + ``document_chunks`` via ``asyncpg`` and:

  1. Inserts each ``documents`` row into the SQLite database (idempotent —
     existing ids are skipped).
  2. Upserts each ``document_chunks`` row into the configured Qdrant
     collection. The stored ``embedding`` JSON is reused as the dense vector;
     the sparse BM25 vector is re-derived locally via FastEmbed.

Idempotent — re-running upserts the same ``chunk_id`` values into Qdrant
(no-op when content is unchanged) and ``INSERT OR IGNORE``s into SQLite.

Usage::

    uv run --with asyncpg python scripts/migrate_pg_to_qdrant.py \\
        --pg-dsn postgresql://docs_rag:pw@localhost:5433/docs_rag \\
        --sqlite-path ./data/app.db \\
        --qdrant-url https://xxx.cloud.qdrant.io \\
        --qdrant-api-key <key>

The script is the ONLY place in the migrated codebase that depends on
asyncpg, so it's installed ad-hoc via ``uv run --with asyncpg`` rather than
pinned as a main dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("migrate_pg_to_qdrant")


async def _read_postgres_rows(pg_dsn: str) -> tuple[list[dict], list[dict]]:
    """Pull documents + document_chunks from the legacy Postgres install."""
    import asyncpg  # type: ignore[import-not-found]

    conn = await asyncpg.connect(pg_dsn)
    try:
        docs = await conn.fetch("SELECT * FROM documents")
        chunks = await conn.fetch(
            "SELECT id, document_id, content, embedding, chunk_index, "
            "section_path, anchor, char_start, char_end, source_type "
            "FROM document_chunks"
        )
    finally:
        await conn.close()

    documents = []
    for row in docs:
        d = dict(row)
        meta = d.get("metadata")
        if isinstance(meta, str):
            d["metadata"] = json.loads(meta) if meta else {}
        documents.append(d)

    chunk_rows = []
    for row in chunks:
        d = dict(row)
        if isinstance(d.get("embedding"), str):
            d["embedding"] = json.loads(d["embedding"])
        if isinstance(d.get("section_path"), str):
            d["section_path"] = json.loads(d["section_path"])
        chunk_rows.append(d)

    return documents, chunk_rows


async def _write_sqlite_documents(sqlite_path: str, documents: list[dict]) -> int:
    """INSERT OR IGNORE documents into the SQLite store. Returns inserted count."""
    import aiosqlite

    inserted = 0
    parent = os.path.dirname(sqlite_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    async with aiosqlite.connect(sqlite_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        for d in documents:
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO documents (
                    id, title, description, url, content_path, source_type,
                    lang, etag, last_modified, content_hash, metadata,
                    last_crawled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(d["id"]),
                    d.get("title") or "",
                    d.get("description") or "",
                    d.get("url"),
                    d.get("content_path"),
                    d.get("source_type") or "firstspirit",
                    d.get("lang"),
                    d.get("etag"),
                    d.get("last_modified"),
                    d.get("content_hash"),
                    json.dumps(d.get("metadata") or {}),
                    _iso(d.get("last_crawled_at")),
                    _iso(d.get("created_at")),
                    _iso(d.get("updated_at")),
                ),
            )
            if cur.rowcount:
                inserted += 1
        await conn.commit()
    return inserted


async def _write_sqlite_chunks(sqlite_path: str, chunks: list[dict]) -> int:
    """INSERT OR REPLACE chunks (without embedding column) into SQLite."""
    import aiosqlite

    written = 0
    async with aiosqlite.connect(sqlite_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        for c in chunks:
            await conn.execute(
                """
                INSERT OR REPLACE INTO document_chunks (
                    id, document_id, content, chunk_index,
                    section_path, anchor, char_start, char_end, source_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(c["id"]),
                    str(c["document_id"]),
                    c.get("content") or "",
                    int(c.get("chunk_index") or 0),
                    json.dumps(c.get("section_path") or []),
                    c.get("anchor"),
                    int(c.get("char_start") or 0),
                    int(c.get("char_end") or 0),
                    c.get("source_type") or "firstspirit",
                ),
            )
            written += 1
        await conn.commit()
    return written


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


async def _upsert_qdrant(
    *,
    documents: list[dict],
    chunks: list[dict],
) -> int:
    """Upsert chunks into Qdrant, re-deriving sparse BM25 via FastEmbed."""
    # Pull config + the vector_store helpers — both rely on env vars being set.
    from backend.rag import vector_store

    docs_by_id = {str(d["id"]): d for d in documents}
    # Group by document_id so each call is one upsert per document (matching
    # the runtime ingest path).
    by_doc: dict[str, list[dict]] = {}
    for c in chunks:
        doc_id = str(c["document_id"])
        doc = docs_by_id.get(doc_id, {})
        by_doc.setdefault(doc_id, []).append(
            {
                "chunk_id": str(c["id"]),
                "content": c.get("content") or "",
                "embedding": c["embedding"],
                "chunk_index": int(c.get("chunk_index") or 0),
                "section_path": c.get("section_path") or [],
                "anchor": c.get("anchor"),
                "char_start": int(c.get("char_start") or 0),
                "char_end": int(c.get("char_end") or 0),
                "source_type": c.get("source_type") or "firstspirit",
                "document_title": doc.get("title") or "",
                "document_url": doc.get("url"),
                "document_content_path": doc.get("content_path"),
            }
        )

    await vector_store.ensure_collection()
    total = 0
    for document_id, doc_chunks in by_doc.items():
        await vector_store.upsert_chunks(document_id=document_id, chunks=doc_chunks)
        total += len(doc_chunks)
        logger.info("Upserted %d chunks for %s", len(doc_chunks), document_id)
    return total


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate_pg_to_qdrant",
        description="One-shot migration from Postgres+pgvector to SQLite + Qdrant Cloud.",
    )
    p.add_argument(
        "--pg-dsn",
        required=True,
        help="Postgres connection string for the legacy install (asyncpg form).",
    )
    p.add_argument(
        "--sqlite-path",
        required=True,
        help="Destination SQLite file path (will be created if missing).",
    )
    p.add_argument(
        "--qdrant-url",
        required=True,
        help="Qdrant Cloud cluster URL. Also settable via QDRANT_URL.",
    )
    p.add_argument(
        "--qdrant-api-key",
        default=None,
        help="Qdrant API key. Also settable via QDRANT_API_KEY.",
    )
    p.add_argument(
        "--qdrant-collection",
        default=None,
        help="Qdrant collection name (default: firstspirit_docs).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read from Postgres and report counts; do not write anywhere.",
    )
    return p


async def _main(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Inject Qdrant config via env so backend.rag.vector_store picks it up.
    os.environ["QDRANT_URL"] = args.qdrant_url
    if args.qdrant_api_key:
        os.environ["QDRANT_API_KEY"] = args.qdrant_api_key
    if args.qdrant_collection:
        os.environ["QDRANT_COLLECTION"] = args.qdrant_collection
    # The backend config also expects DATABASE_URL; supply a placeholder so
    # importing backend.config doesn't print a warning.
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{args.sqlite_path}")

    # Make backend importable when running directly from the repo root.
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "app"))

    logger.info("Reading from Postgres: %s", args.pg_dsn)
    documents, chunks = await _read_postgres_rows(args.pg_dsn)
    logger.info("Read %d documents and %d chunks.", len(documents), len(chunks))

    if args.dry_run:
        logger.info("Dry run — skipping writes.")
        return 0

    logger.info("Writing documents to SQLite: %s", args.sqlite_path)
    inserted = await _write_sqlite_documents(args.sqlite_path, documents)
    logger.info("Inserted %d new document rows.", inserted)

    logger.info("Writing chunks to SQLite.")
    written = await _write_sqlite_chunks(args.sqlite_path, chunks)
    logger.info("Wrote %d chunk rows.", written)

    logger.info("Upserting chunks to Qdrant.")
    total = await _upsert_qdrant(documents=documents, chunks=chunks)
    logger.info("Done. %d chunks upserted across %d documents.", total, len(documents))
    return 0


def main() -> None:
    args = _parser().parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()

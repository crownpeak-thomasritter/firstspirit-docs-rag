"""
Vault ingester — walks a directory of Obsidian markdown notes.

Reads every ``*.md`` file under ``SOURCE_VAULT_PATH`` (recursive), parses the
optional YAML frontmatter with :mod:`python_frontmatter`, then hands the body
to the chunker + embedder. Each file is keyed by its vault-relative path
(stored in ``documents.content_path``).

Idempotency:
    The raw file's SHA-256 is stored in ``documents.content_hash``. A second
    sync over an unchanged file is essentially free — we re-hash and skip the
    chunk + embed work.

The pipeline is offline — no HTTP, no robots.txt. ``url`` is set to the
``source`` frontmatter key if present (so a note imported from a web page can
still cite the original), otherwise NULL.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

import frontmatter

from backend.config import DEFAULT_SOURCE_TYPE, SOURCE_VAULT_PATH
from backend.db import repository
from backend.rag import document_chunker, vector_store
from backend.rag.embeddings import embed_batch
from backend.services import extractor

logger = logging.getLogger(__name__)


def _iter_markdown_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if p.is_file())


async def sync_vault(
    *,
    vault_path: str | None = None,
    source_type: str = DEFAULT_SOURCE_TYPE,
) -> dict:
    """Run one ingestion pass over the markdown vault.

    Args:
        vault_path: Override the configured ``SOURCE_VAULT_PATH``. Useful in
            tests.
        source_type: Tag applied to every document + chunk row.

    Returns:
        The final sync-run summary row.
    """
    raw_path = vault_path if vault_path is not None else SOURCE_VAULT_PATH
    if not raw_path:
        raise ValueError("SOURCE_VAULT_PATH is not configured.")

    root = Path(raw_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Vault directory not found: {root}")

    files = _iter_markdown_files(root)
    logger.info("Starting vault sync from %s with %d files", root, len(files))

    started = repository._now()
    sync_run_id = repository._new_id()
    await repository.create_sync_run(sync_run_id=sync_run_id, kind="vault", started_at=started)

    new = 0
    updated = 0
    unchanged = 0
    errors = 0

    for path in files:
        rel_path = path.relative_to(root).as_posix()
        item = await repository.create_sync_item(sync_run_id=sync_run_id, source_ref=rel_path)
        try:
            outcome = await _ingest_one_file(path, rel_path, source_type=source_type)
        except Exception as exc:
            logger.exception("Vault file %s failed: %s", rel_path, exc)
            await repository.update_sync_item_outcome(item["id"], "error", str(exc))
            errors += 1
            continue

        await repository.update_sync_item_outcome(item["id"], outcome)
        if outcome == "ingested":
            new += 1
        elif outcome == "updated":
            updated += 1
        elif outcome == "unchanged":
            unchanged += 1
        else:
            errors += 1

    finished = repository._now()
    await repository.update_sync_run(
        sync_run_id=sync_run_id,
        status="completed",
        finished_at=finished,
        items_total=len(files),
        items_new=new,
        items_updated=updated,
        items_unchanged=unchanged,
        items_error=errors,
    )

    return {
        "sync_run_id": sync_run_id,
        "kind": "vault",
        "status": "completed",
        "items_total": len(files),
        "items_new": new,
        "items_updated": updated,
        "items_unchanged": unchanged,
        "items_error": errors,
        "started_at": started,
        "finished_at": finished,
    }


async def _ingest_one_file(path: Path, rel_path: str, *, source_type: str) -> str:
    """Read + extract + chunk + embed one vault file."""
    raw_bytes = path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    existing = await repository.get_document_by_content_path(rel_path)
    if existing and existing.get("content_hash") == content_hash:
        return "unchanged"

    # frontmatter.loads accepts text; decode tolerantly so a stray non-UTF-8
    # byte from the vault doesn't blow up the whole run.
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="replace")

    post = frontmatter.loads(text)
    metadata: dict[str, Any] = dict(post.metadata or {})
    body = post.content or ""

    if not body.strip():
        logger.info("Vault file %s has empty body — skipping", rel_path)
        return "error"

    extracted = extractor.extract_markdown(body.encode("utf-8"))
    if extracted is None:
        return "error"

    title = metadata.get("title") or extracted.title or path.stem
    description = str(metadata.get("description") or "")
    lang = metadata.get("lang") or extracted.lang
    source_url = metadata.get("source") or metadata.get("url")
    source_url_str = str(source_url) if source_url else None

    chunks, _had_errors = document_chunker.chunk_document(extracted)
    if not chunks:
        return "error"

    embeddings = await asyncio.to_thread(embed_batch, [c.content for c in chunks])

    chunk_payload = [
        {
            "chunk_id": repository._new_id(),
            "content": c.content,
            "embedding": emb,
            "chunk_index": c.chunk_index,
            "section_path": c.section_path,
            "anchor": c.anchor,
            "char_start": c.char_start,
            "char_end": c.char_end,
        }
        for c, emb in zip(chunks, embeddings, strict=True)
    ]

    if existing:
        await repository.update_document_crawl_metadata(
            existing["id"],
            title=str(title),
            description=description,
            lang=str(lang) if lang else None,
            content_hash=content_hash,
            metadata=metadata,
        )
        document_id = existing["id"]
        outcome = "updated"
    else:
        doc = await repository.create_document(
            title=str(title),
            description=description,
            url=source_url_str,
            content_path=rel_path,
            source_type=source_type,
            lang=str(lang) if lang else None,
            content_hash=content_hash,
            metadata=metadata,
        )
        document_id = doc["id"]
        outcome = "ingested"

    await repository.replace_chunks_for_document(
        document_id, chunk_payload, source_type=source_type
    )

    qdrant_chunks = [
        {
            **c,
            "source_type": source_type,
            "document_title": str(title),
            "document_url": source_url_str,
            "document_content_path": rel_path,
        }
        for c in chunk_payload
    ]
    try:
        await vector_store.upsert_chunks(document_id=document_id, chunks=qdrant_chunks)
    except Exception as exc:
        logger.error(
            "Qdrant upsert failed for %s; rolling back SQLite chunks: %s",
            rel_path,
            exc,
        )
        try:
            await vector_store.delete_document(document_id)
        except Exception as cleanup_exc:
            logger.warning("Qdrant cleanup also failed for %s: %s", rel_path, cleanup_exc)
        await repository.replace_chunks_for_document(document_id, [], source_type=source_type)
        raise

    return outcome

"""
URL-list ingester — crawls a flat list of documentation URLs.

The list is a markdown file (default ``./URL List.md``) with one URL per line.
Lines that are empty, start with ``#``, or are markdown headings are ignored.
For each URL:

1. Look up the existing :class:`document` row (if any) for its etag /
   last_modified — these get sent back as conditional-GET headers.
2. ``crawler.fetch`` runs the polite GET with retries + robots.txt respect.
3. On ``OK``: extract via :func:`backend.services.extractor.extract`, chunk
   via :func:`backend.rag.document_chunker.chunk_document`, batch-embed the
   chunk bodies, and atomically swap the document's chunks via
   :func:`backend.db.repository.replace_chunks_for_document`.
4. On ``NOT_MODIFIED``: just refresh ``last_crawled_at`` (and any updated
   etag / last_modified the server returned).
5. On ``ERROR`` / ``SKIPPED_ROBOTS``: record the outcome on the sync item.

The whole run is wrapped in a :class:`source_sync_runs` row so the admin UI
can show what changed in the last run. The pipeline is idempotent — re-running
without source changes is cheap thanks to ETag.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

from backend.config import DEFAULT_SOURCE_TYPE, SOURCE_URL_LIST_PATH
from backend.db import repository
from backend.rag import document_chunker, vector_store
from backend.rag.embeddings import embed_batch
from backend.services import crawler, extractor

logger = logging.getLogger(__name__)


# A markdown link looks like ``[label](https://example.com)``. The URL list
# file is allowed to be raw URLs or bullets / markdown links — we accept all.
_URL_RE = re.compile(r"https?://\S+")


def parse_url_list(text: str) -> list[str]:
    """Parse a URL List markdown file into a deduplicated list of URLs.

    Supports raw lines (``https://...``), markdown bullets, and link syntax.
    Strips trailing punctuation that almost always denotes prose, not URL:
    ``.,);]"`` and the closing ``)`` from a markdown link target.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,);]\"'")
            if url not in seen:
                seen.add(url)
                out.append(url)
    return out


async def sync_url_list(
    *,
    list_path: str | None = None,
    source_type: str = DEFAULT_SOURCE_TYPE,
) -> dict:
    """Run one ingestion pass over the URL list. Returns the sync-run summary.

    Args:
        list_path: Override the configured ``SOURCE_URL_LIST_PATH``. Useful in
            tests.
        source_type: Tag applied to every document + chunk row created here.

    Returns:
        The final :class:`source_sync_runs` row with item counts populated.
    """
    path = Path(list_path or SOURCE_URL_LIST_PATH)
    if not path.exists():
        raise FileNotFoundError(f"URL list not found: {path}")

    urls = parse_url_list(path.read_text(encoding="utf-8"))
    logger.info("Starting URL-list sync from %s with %d URLs", path, len(urls))

    started = repository._now()
    sync_run_id = repository._new_id()
    await repository.create_sync_run(sync_run_id=sync_run_id, kind="url_list", started_at=started)

    new = 0
    updated = 0
    unchanged = 0
    errors = 0

    for url in urls:
        item = await repository.create_sync_item(sync_run_id=sync_run_id, source_ref=url)
        error_message: str | None = None
        try:
            outcome, error_message = await _ingest_one_url(url, source_type=source_type)
        except Exception as exc:  # never let one URL kill the run
            logger.exception("URL %s failed unhandled: %s", url, exc)
            outcome = "error"
            error_message = str(exc)

        await repository.update_sync_item_outcome(item["id"], outcome, error_message)
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
        items_total=len(urls),
        items_new=new,
        items_updated=updated,
        items_unchanged=unchanged,
        items_error=errors,
    )

    return {
        "sync_run_id": sync_run_id,
        "kind": "url_list",
        "status": "completed",
        "items_total": len(urls),
        "items_new": new,
        "items_updated": updated,
        "items_unchanged": unchanged,
        "items_error": errors,
        "started_at": started,
        "finished_at": finished,
    }


async def _ingest_one_url(url: str, *, source_type: str) -> tuple[str, str | None]:
    """Crawl + extract + chunk + embed one URL.

    Returns a ``(outcome, error_message)`` tuple. ``error_message`` is None for
    successful outcomes and carries a short human-readable reason for ``error``.
    """
    existing = await repository.get_document_by_url(url)
    etag = existing.get("etag") if existing else None
    last_modified = existing.get("last_modified") if existing else None

    result = await crawler.fetch(url, etag=etag, last_modified=last_modified)

    if result.status is crawler.CrawlStatus.SKIPPED_ROBOTS:
        logger.info("Skipped by robots: %s", url)
        return "error", "skipped by robots.txt"

    if result.status is crawler.CrawlStatus.NOT_MODIFIED:
        if existing:
            await repository.update_document_crawl_metadata(
                existing["id"],
                etag=result.etag,
                last_modified=result.last_modified,
            )
        return "unchanged", None

    if result.status is crawler.CrawlStatus.ERROR:
        logger.warning("Fetch error for %s: %s", url, result.error)
        return "error", f"fetch failed: {result.error or 'unknown error'}"

    # status == OK — we have fresh bytes
    if result.content is None:
        return "error", "crawler returned OK with empty body"

    extracted = extractor.extract(
        result.content,
        content_type=result.content_type,
        source_url=url,
    )
    if extracted is None:
        logger.info("Extractor yielded no body for %s", url)
        return "error", "extractor yielded no body (possibly JS-rendered or empty page)"

    content_hash = hashlib.sha256(result.content).hexdigest()

    # Cheap idempotency: if the raw body hashes the same as what we already
    # stored, skip chunking + embedding entirely. This catches the case where
    # the upstream server didn't honor our If-None-Match but the page didn't
    # actually change.
    if existing and existing.get("content_hash") == content_hash:
        await repository.update_document_crawl_metadata(
            existing["id"],
            etag=result.etag,
            last_modified=result.last_modified,
            content_hash=content_hash,
        )
        return "unchanged", None

    chunks, _had_chunk_errors = document_chunker.chunk_document(extracted)
    if not chunks:
        logger.info("Chunker produced 0 chunks for %s", url)
        return "error", "chunker produced 0 chunks"

    embeddings = await asyncio.to_thread(embed_batch, [c.content for c in chunks])

    # Generate chunk_ids client-side so the same id lands in both SQLite and
    # Qdrant. The SQLite repository accepts the supplied chunk_id; the Qdrant
    # vector_store uses it as the point id.
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

    title = extracted.title or (existing.get("title") if existing else None) or url

    if existing:
        await repository.update_document_crawl_metadata(
            existing["id"],
            title=title,
            lang=extracted.lang,
            etag=result.etag,
            last_modified=result.last_modified,
            content_hash=content_hash,
        )
        document_id = existing["id"]
        outcome = "updated"
        newly_created = False
    else:
        doc = await repository.create_document(
            title=title,
            description="",
            url=url,
            source_type=source_type,
            lang=extracted.lang,
            etag=result.etag,
            last_modified=result.last_modified,
            content_hash=content_hash,
        )
        document_id = doc["id"]
        outcome = "ingested"
        newly_created = True

    await repository.replace_chunks_for_document(
        document_id, chunk_payload, source_type=source_type
    )

    # Augment chunks with document metadata so Qdrant payloads carry it (no
    # second SQLite lookup at retrieval time).
    qdrant_chunks = [
        {
            **c,
            "source_type": source_type,
            "document_title": title,
            "document_url": url,
            "document_content_path": None,
        }
        for c in chunk_payload
    ]
    try:
        await vector_store.upsert_chunks(document_id=document_id, chunks=qdrant_chunks)
    except Exception as exc:
        # Roll back so SQLite + Qdrant + future retries stay consistent.
        #   1. Best-effort delete of any Qdrant points the partial upsert created.
        #   2. SQLite: if this was a fresh document, drop the row (CASCADE wipes
        #      chunks); otherwise just clear the chunks but keep the doc row.
        #      Either way, also clear ETag / Last-Modified / content_hash on the
        #      surviving row so a conditional GET on the next sync re-fetches
        #      instead of 304'ing on the stale identifiers we never committed.
        logger.error("Qdrant upsert failed for %s; rolling back: %s", url, exc)
        try:
            await vector_store.delete_document(document_id)
        except Exception as cleanup_exc:
            logger.warning("Qdrant cleanup also failed for %s: %s", url, cleanup_exc)
        if newly_created:
            await repository.delete_document_cascade(document_id)
        else:
            await repository.replace_chunks_for_document(
                document_id, [], source_type=source_type
            )
            await repository.clear_document_crawl_state(document_id)
        raise

    return outcome, None

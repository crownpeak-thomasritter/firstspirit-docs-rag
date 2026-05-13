"""Document catalog cache — formatted catalog block for the system prompt.

Maintains an in-process cache of document metadata from the DB. The cache is
invalidated whenever new documents are ingested or a sync completes, keeping
the catalog block fresh without hitting the DB on every chat request.
"""

from __future__ import annotations

import logging

from backend.config import CATALOG_CACHE_TTL_SECONDS
from backend.db import repository

logger = logging.getLogger(__name__)

_catalog_cache: list[dict] | None = None


async def get_catalog() -> list[dict]:
    """Return the cached document list, fetching from the DB on first call.

    Returns an empty list on DB error so callers degrade gracefully.
    """
    global _catalog_cache
    if _catalog_cache is None:
        try:
            _catalog_cache = await repository.list_documents()
            logger.info("Document catalog cache populated with %d documents.", len(_catalog_cache))
        except Exception:
            logger.warning(
                "Failed to populate document catalog cache; skipping catalog block.",
                exc_info=True,
            )
            _catalog_cache = []  # prevent retry storm; cleared by next invalidate_catalog()
    return _catalog_cache


def invalidate_catalog() -> None:
    """Clear the in-process catalog cache so the next call re-fetches."""
    global _catalog_cache
    _catalog_cache = None
    logger.info("Document catalog cache invalidated.")


def build_catalog_block(documents: list[dict], tier: str, *, cache: bool = True) -> dict:
    """Format the document list as a content block.

    Each entry includes the internal ``id`` so the model can pass it directly
    to ``get_document`` when a user references a page by name or topic.

    Args:
        documents: List of document dicts with ``id`` and ``title``; ``url``
            and ``content_path`` are both optional.
        tier: ``"standard"`` (~5-min ephemeral) or ``"extended"`` (1-hour TTL).
        cache: When True (default) and the request goes via OpenRouter to an
            Anthropic model, attach a ``cache_control`` block so the catalog
            is cached. Pass ``False`` for OpenAI native — the OpenAI API
            rejects extra keys with HTTP 400, and OpenAI's automatic prompt
            cache works transparently without an explicit block.

    Returns:
        A content block dict suitable for inclusion in the system message's
        content array.
    """
    lines = [
        "Available documents in the library. Use `id=...` directly with "
        "get_document when a user references a page by title, topic, or "
        "identifier.",
        "",
    ]
    for idx, d in enumerate(documents, 1):
        title = d.get("title") or "Untitled"
        url = d.get("url") or d.get("content_path") or ""
        doc_id = d.get("id", "")
        url_part = f" — {url}" if url else ""
        lines.append(f"{idx}. {title} (id={doc_id}){url_part}")

    block: dict = {
        "type": "text",
        "text": "\n".join(lines),
    }
    if cache:
        cache_control: dict = {"type": "ephemeral"}
        if tier == "extended":
            cache_control["ttl"] = CATALOG_CACHE_TTL_SECONDS
        block["cache_control"] = cache_control
    return block

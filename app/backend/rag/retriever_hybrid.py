"""Hybrid retriever — thin shim over ``rag.vector_store.hybrid_search``.

Server-side RRF (k=60, fixed) is done by Qdrant via the Query API; this
module exists only as a stable public surface (callers in ``rag/tools.py``
and ``routes/sources.py`` import from here) and as the hook point for any
future ranking / post-processing layers.

The hit shape returned by ``vector_store.hybrid_search`` already includes
document metadata (title, url, source_type, anchor, section_path, …) on
each row's payload, so the previous in-process document-metadata cache is
gone.
"""

from __future__ import annotations

import logging

from backend.config import DEFAULT_SOURCE_TYPE
from backend.rag import vector_store

logger = logging.getLogger(__name__)


def invalidate_cache() -> None:
    """No-op now — Qdrant payloads carry document metadata; no in-process cache.

    Retained as a public function because ``routes/sources.py`` calls it
    after ingest. Future caching layers can hook here.
    """
    logger.debug("retriever_hybrid.invalidate_cache called (no-op).")


async def retrieve_hybrid(
    query_text: str,
    query_embedding: list[float],
    top_k: int = 5,
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Hybrid retrieval via Qdrant's server-side RRF.

    Args:
        query_text: Original user query string (used for BM25 sparse vector).
        query_embedding: Query embedding from ``embed_text()`` (used for the
            dense vector half).
        top_k: Maximum number of results to return (default 5).
        allowed_source_types: ACL filter. ``None`` defaults to
            ``[DEFAULT_SOURCE_TYPE]``. Extra source types reserved for future
            multi-tier corpora.

    Returns:
        List of dicts (length ≤ ``top_k``) with canonical hit-shape keys
        (see ``rag.vector_store._hit_to_dict``).
    """
    if allowed_source_types is None:
        allowed_source_types = [DEFAULT_SOURCE_TYPE]
    return await vector_store.hybrid_search(
        query_text=query_text,
        query_embedding=query_embedding,
        top_k=top_k,
        allowed_source_types=allowed_source_types,
    )

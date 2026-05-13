"""
Hybrid retriever — Reciprocal Rank Fusion (RRF) over keyword and vector search.

Combines Postgres tsvector full-text search with pgvector cosine similarity
using RRF to produce a unified ranking. Each method over-fetches ``2*top_k``
candidates before merging (standard RRF practice).

Requires Postgres with:
  - ``search_vector`` tsvector column on ``document_chunks`` (GENERATED ALWAYS AS)
  - GIN index on ``search_vector``
  - pgvector extension loaded

Falls back to raising a clear error if ``DATABASE_URL`` is not set (no silent
cosine fallback).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from backend.config import (
    DEFAULT_SOURCE_TYPE,
    HYBRID_K_CONSTANT,
    HYBRID_OVERFETCH_FACTOR,
    KEYWORD_LANGUAGE,
)
from backend.db import repository

logger = logging.getLogger(__name__)

# Module-level document metadata cache (populated on demand per chunk result).
_document_cache: dict[str, dict[str, str | None]] = {}


def invalidate_cache() -> None:
    """Clear the document metadata cache."""
    global _document_cache
    _document_cache.clear()
    logger.info("Hybrid retriever document cache invalidated.")


async def retrieve_hybrid(
    query_text: str,
    query_embedding: list[float],
    top_k: int = 5,
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Hybrid retrieval via Reciprocal Rank Fusion (RRF).

    Args:
        query_text: Original user query string (used for keyword search).
        query_embedding: Query embedding from ``embed_text()`` (used for
            vector search).
        top_k: Maximum number of results to return (default 5).
        allowed_source_types: ACL filter. ``None`` defaults to
            ``[DEFAULT_SOURCE_TYPE]``. Extra source types reserved for future
            multi-tier corpora (e.g. paid content) — see plan §NOT_BUILDING.

    Returns:
        List of dicts (length ≤ ``top_k``) with keys:
          - ``chunk_id`` (str)
          - ``content`` (str)
          - ``document_id`` (str)
          - ``document_title`` (str)
          - ``document_url`` (str | None)
          - ``document_content_path`` (str | None)
          - ``source_type`` (str)
          - ``section_path`` (list[str])
          - ``anchor`` (str | None)
          - ``chunk_index`` (int)
          - ``score`` (float) — RRF score, higher is better

    Raises:
        RuntimeError: If ``DATABASE_URL`` is not set.
    """
    from backend.config import DATABASE_URL

    if not DATABASE_URL:
        raise RuntimeError("Hybrid retrieval requires Postgres (DATABASE_URL is not set).")

    if allowed_source_types is None:
        allowed_source_types = [DEFAULT_SOURCE_TYPE]

    fetch_k = top_k * HYBRID_OVERFETCH_FACTOR

    keyword_task = repository.keyword_search(
        query_text,
        top_k=fetch_k,
        language=KEYWORD_LANGUAGE,
        allowed_source_types=allowed_source_types,
    )
    vector_task = repository.vector_search_pg(
        query_embedding,
        top_k=fetch_k,
        allowed_source_types=allowed_source_types,
    )
    keyword_hits, vector_hits = await keyword_task, await vector_task

    logger.debug(
        "Hybrid retrieval: %d keyword hits, %d vector hits (fetch_k=%d)",
        len(keyword_hits),
        len(vector_hits),
        fetch_k,
    )

    if not keyword_hits and not vector_hits:
        return []

    merged = _rrf_merge(keyword_hits, vector_hits, k=HYBRID_K_CONSTANT, top_k=top_k)

    results: list[dict] = []
    for chunk in merged:
        document_id = chunk["document_id"]
        if document_id not in _document_cache:
            doc = await repository.get_document(document_id)
            if doc:
                _document_cache[document_id] = {
                    "title": doc.get("title") or "Untitled",
                    "url": doc.get("url"),
                    "content_path": doc.get("content_path"),
                    "source_type": doc.get("source_type") or DEFAULT_SOURCE_TYPE,
                }
            else:
                logger.warning(
                    "Document not found for document_id=%s, chunk_id=%s",
                    document_id,
                    chunk.get("id", "?"),
                )
                _document_cache[document_id] = {
                    "title": "Unknown Document",
                    "url": None,
                    "content_path": None,
                    "source_type": DEFAULT_SOURCE_TYPE,
                }

        meta = _document_cache[document_id]
        results.append(
            {
                "chunk_id": chunk["id"],
                "content": chunk["content"],
                "document_id": document_id,
                "document_title": meta["title"],
                "document_url": meta["url"],
                "document_content_path": meta["content_path"],
                "source_type": meta["source_type"],
                "section_path": chunk.get("section_path") or [],
                "anchor": chunk.get("anchor"),
                "chunk_index": chunk.get("chunk_index", 0),
                "score": chunk.get("rrf_score", 0.0),
            }
        )

    return results


def _rrf_merge(
    keyword_hits: list[dict],
    vector_hits: list[dict],
    k: int = 60,
    top_k: int = 5,
) -> list[dict]:
    """Reciprocal Rank Fusion merge of two ranked lists.

    RRF score = Σ 1 / (k + rank_i) summed across all input methods, where
    ``rank_i`` is the 0-based position of the item in method i's results.
    Higher score = better merged rank. ``k=60`` is the canonical constant
    from the original RRF paper.
    """
    scores: dict[str, float] = defaultdict(float)
    rows: dict[str, dict] = {}

    for rank, row in enumerate(keyword_hits):
        chunk_id = row["id"]
        scores[chunk_id] += 1.0 / (k + rank)
        rows[chunk_id] = row

    for rank, row in enumerate(vector_hits):
        chunk_id = row["id"]
        scores[chunk_id] += 1.0 / (k + rank)
        if chunk_id not in rows:
            rows[chunk_id] = row

    ranked = sorted(scores, key=scores.__getitem__, reverse=True)[:top_k]
    return [{**rows[cid], "rrf_score": scores[cid]} for cid in ranked]

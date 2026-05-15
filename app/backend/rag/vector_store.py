"""Qdrant-backed vector store with server-side hybrid search (dense + BM25).

One collection (``QDRANT_COLLECTION``, default ``firstspirit_docs``) holds
named vectors:
  - ``dense``: ``EMBEDDING_DIM``-dim, cosine distance — OpenAI ``text-embedding-3-small``
  - ``bm25``: sparse, IDF modifier — Qdrant/bm25 via FastEmbed

Hybrid retrieval uses the Query API with ``prefetch + fusion=RRF``, replacing
the in-process RRF that was formerly done in ``rag/retriever_hybrid.py``. RRF
``k`` is fixed at 60 by Qdrant (matches the value used before).

Chunk metadata (title, url, section path, anchor, source_type, …) is stored
on each point payload so retrieval returns everything citations need in one
round-trip; no second SQLite query is needed for hit hydration.
"""

from __future__ import annotations

import logging
from typing import Any

from fastembed import SparseTextEmbedding
from qdrant_client import AsyncQdrantClient, models

from backend.config import (
    DEFAULT_SOURCE_TYPE,
    EMBEDDING_DIM,
    QDRANT_API_KEY,
    QDRANT_BM25_MODEL,
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    QDRANT_URL,
)

logger = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None
_bm25: SparseTextEmbedding | None = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        if not QDRANT_URL:
            raise RuntimeError("QDRANT_URL is not set.")
        _client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    return _client


def _get_bm25() -> SparseTextEmbedding:
    global _bm25
    if _bm25 is None:
        _bm25 = SparseTextEmbedding(model_name=QDRANT_BM25_MODEL)
    return _bm25


async def ensure_collection() -> None:
    """Create the collection if it doesn't exist. Idempotent — called from lifespan."""
    client = _get_client()
    try:
        exists = await client.collection_exists(QDRANT_COLLECTION)
    except Exception as exc:
        logger.error("Qdrant collection_exists check failed: %s", exc)
        raise RuntimeError(f"Qdrant collection_exists failed: {exc}") from exc
    if exists:
        return
    try:
        await client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config={
                QDRANT_DENSE_VECTOR_NAME: models.VectorParams(
                    size=EMBEDDING_DIM,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                QDRANT_SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                ),
            },
        )
        # Payload indexes for filtering / per-document grouping.
        await client.create_payload_index(
            QDRANT_COLLECTION,
            field_name="document_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        await client.create_payload_index(
            QDRANT_COLLECTION,
            field_name="source_type",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    except Exception as exc:
        logger.error("Qdrant create_collection failed: %s", exc)
        raise RuntimeError(f"Qdrant create_collection failed: {exc}") from exc
    logger.info("Created Qdrant collection: %s", QDRANT_COLLECTION)


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception as exc:
            logger.warning("Qdrant client close error (ignored): %s", exc)
        _client = None


async def upsert_chunks(
    *,
    document_id: str,
    chunks: list[dict[str, Any]],
) -> None:
    """Upsert points for one document. ``chunk_id`` is the Qdrant point id.

    Each chunk dict must include: ``chunk_id``, ``content``, ``embedding``
    (dense vector), ``chunk_index``. Optional: ``section_path``, ``anchor``,
    ``char_start``, ``char_end``, ``source_type``, ``document_title``,
    ``document_url``, ``document_content_path``.
    """
    if not chunks:
        return
    client = _get_client()
    bm25 = _get_bm25()
    texts = [c["content"] for c in chunks]
    try:
        sparse_vectors = list(bm25.embed(texts))
    except Exception as exc:
        logger.error("BM25 embed failed: %s", exc)
        raise RuntimeError(f"BM25 sparse embedding failed: {exc}") from exc

    points: list[models.PointStruct] = []
    for c, sparse in zip(chunks, sparse_vectors, strict=True):
        points.append(
            models.PointStruct(
                id=c["chunk_id"],
                vector={
                    QDRANT_DENSE_VECTOR_NAME: c["embedding"],
                    QDRANT_SPARSE_VECTOR_NAME: models.SparseVector(
                        indices=sparse.indices.tolist(),
                        values=sparse.values.tolist(),
                    ),
                },
                payload={
                    "chunk_id": c["chunk_id"],
                    "document_id": document_id,
                    "content": c["content"],
                    "chunk_index": c["chunk_index"],
                    "section_path": c.get("section_path", []),
                    "anchor": c.get("anchor"),
                    "char_start": c.get("char_start", 0),
                    "char_end": c.get("char_end", 0),
                    "source_type": c.get("source_type", DEFAULT_SOURCE_TYPE),
                    "document_title": c.get("document_title", ""),
                    "document_url": c.get("document_url"),
                    "document_content_path": c.get("document_content_path"),
                },
            )
        )
    # Batch the upsert. Qdrant's default REST body limit is 32 MB and a single
    # 1536-dim dense vector + sparse vector + chunk text easily exceeds 15 KB,
    # so a big PDF (~2k chunks) overruns the cap when sent in one request.
    batch_size = 200
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        try:
            await client.upsert(collection_name=QDRANT_COLLECTION, points=batch, wait=True)
        except Exception as exc:
            # qdrant-client wraps the HTTP body in repr(exc), but the
            # exception's __str__ is sometimes empty — fall back to repr so
            # the sync_run error_message is never blank.
            detail = str(exc) or repr(exc)
            logger.error(
                "Qdrant upsert failed for document_id=%s (batch %d-%d of %d): %s",
                document_id,
                start,
                start + len(batch),
                len(points),
                detail,
            )
            raise RuntimeError(f"Qdrant upsert failed: {detail}") from exc


async def delete_document(document_id: str) -> None:
    client = _get_client()
    try:
        await client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        )
                    ],
                ),
            ),
            wait=True,
        )
    except Exception as exc:
        logger.error("Qdrant delete failed for document_id=%s: %s", document_id, exc)
        raise RuntimeError(f"Qdrant delete failed: {exc}") from exc


async def count() -> int:
    client = _get_client()
    try:
        result = await client.count(collection_name=QDRANT_COLLECTION, exact=True)
    except Exception as exc:
        logger.error("Qdrant count failed: %s", exc)
        raise RuntimeError(f"Qdrant count failed: {exc}") from exc
    return int(result.count)


def _source_type_filter(allowed: list[str] | None) -> models.Filter | None:
    if not allowed:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="source_type",
                match=models.MatchAny(any=allowed),
            )
        ],
    )


def _hit_to_dict(point: Any) -> dict:
    """Map Qdrant ScoredPoint → the canonical hit shape consumed by tools/messages."""
    p = point.payload or {}
    return {
        "chunk_id": p.get("chunk_id") or str(point.id),
        "id": p.get("chunk_id") or str(point.id),
        "content": p.get("content", ""),
        "document_id": p.get("document_id", ""),
        "document_title": p.get("document_title", "Untitled"),
        "document_url": p.get("document_url"),
        "document_content_path": p.get("document_content_path"),
        "source_type": p.get("source_type", DEFAULT_SOURCE_TYPE),
        "section_path": p.get("section_path") or [],
        "anchor": p.get("anchor"),
        "chunk_index": p.get("chunk_index", 0),
        "char_start": p.get("char_start", 0),
        "char_end": p.get("char_end", 0),
        "score": float(getattr(point, "score", 0.0) or 0.0),
    }


async def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    top_k: int = 5,
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Server-side RRF over dense + BM25 sparse. Returns the canonical hit shape."""
    if not query_text or not query_text.strip():
        raise ValueError("hybrid_search() requires a non-empty query_text.")
    client = _get_client()
    bm25 = _get_bm25()
    try:
        sparse = next(iter(bm25.query_embed(query_text)))
    except Exception as exc:
        logger.error("BM25 query embed failed: %s", exc)
        raise RuntimeError(f"BM25 query embedding failed: {exc}") from exc
    from backend.config import HYBRID_OVERFETCH_FACTOR

    fetch_k = top_k * HYBRID_OVERFETCH_FACTOR
    query_filter = _source_type_filter(allowed_source_types)
    try:
        result = await client.query_points(
            collection_name=QDRANT_COLLECTION,
            prefetch=[
                models.Prefetch(
                    query=query_embedding,
                    using=QDRANT_DENSE_VECTOR_NAME,
                    limit=fetch_k,
                    filter=query_filter,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse.indices.tolist(),
                        values=sparse.values.tolist(),
                    ),
                    using=QDRANT_SPARSE_VECTOR_NAME,
                    limit=fetch_k,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.error("Qdrant hybrid_search query_points failed: %s", exc)
        raise RuntimeError(f"Qdrant hybrid_search failed: {exc}") from exc
    return [_hit_to_dict(p) for p in result.points]


async def keyword_search(
    query_text: str,
    top_k: int = 10,
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Sparse-only search (BM25) — used by the keyword_search_documents LLM tool."""
    if not query_text or not query_text.strip():
        raise ValueError("keyword_search() requires a non-empty query_text.")
    client = _get_client()
    bm25 = _get_bm25()
    try:
        sparse = next(iter(bm25.query_embed(query_text)))
    except Exception as exc:
        logger.error("BM25 query embed failed: %s", exc)
        raise RuntimeError(f"BM25 query embedding failed: {exc}") from exc
    try:
        result = await client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=models.SparseVector(
                indices=sparse.indices.tolist(),
                values=sparse.values.tolist(),
            ),
            using=QDRANT_SPARSE_VECTOR_NAME,
            query_filter=_source_type_filter(allowed_source_types),
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.error("Qdrant keyword_search query_points failed: %s", exc)
        raise RuntimeError(f"Qdrant keyword_search failed: {exc}") from exc
    return [_hit_to_dict(p) for p in result.points]


async def semantic_search(
    query_embedding: list[float],
    top_k: int = 10,
    allowed_source_types: list[str] | None = None,
) -> list[dict]:
    """Dense-only search — used by the semantic_search_documents LLM tool."""
    client = _get_client()
    try:
        result = await client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_embedding,
            using=QDRANT_DENSE_VECTOR_NAME,
            query_filter=_source_type_filter(allowed_source_types),
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.error("Qdrant semantic_search query_points failed: %s", exc)
        raise RuntimeError(f"Qdrant semantic_search failed: {exc}") from exc
    return [_hit_to_dict(p) for p in result.points]

"""Tests for ``backend.rag.vector_store``.

The Qdrant client and the FastEmbed sparse encoder are both mocked so the
tests don't need network access or the ~30MB ONNX BM25 model.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.rag import vector_store


class _FakeSparseEmbedding:
    """Fake ``SparseTextEmbedding`` returning ``indices`` + ``values`` arrays."""

    @staticmethod
    def _sparse(indices, values):
        return SimpleNamespace(
            indices=SimpleNamespace(tolist=lambda: list(indices)),
            values=SimpleNamespace(tolist=lambda: list(values)),
        )

    def embed(self, texts):
        for _ in texts:
            yield self._sparse([1, 2], [0.5, 0.5])

    def query_embed(self, text):
        yield self._sparse([1], [1.0])


@pytest.fixture
def fake_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(vector_store, "_client", client)
    monkeypatch.setattr(vector_store, "_bm25", _FakeSparseEmbedding())
    return client


def _scored_point(payload: dict, score: float = 0.42) -> SimpleNamespace:
    """Fake Qdrant ScoredPoint with .payload, .id, .score."""
    return SimpleNamespace(payload=payload, id=payload.get("chunk_id"), score=score)


def _query_response(points: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(points=points)


# ---------------------------------------------------------------------------
# ensure_collection — idempotent
# ---------------------------------------------------------------------------


async def test_ensure_collection_idempotent(fake_client):
    fake_client.collection_exists.return_value = True
    await vector_store.ensure_collection()
    fake_client.create_collection.assert_not_called()


async def test_ensure_collection_creates_when_missing(fake_client):
    fake_client.collection_exists.return_value = False
    await vector_store.ensure_collection()
    fake_client.create_collection.assert_called_once()
    # Both payload indexes for filtering should be created too.
    assert fake_client.create_payload_index.await_count == 2


# ---------------------------------------------------------------------------
# upsert_chunks — writes both dense and sparse vectors
# ---------------------------------------------------------------------------


async def test_upsert_chunks_writes_dense_and_sparse(fake_client):
    await vector_store.upsert_chunks(
        document_id="d1",
        chunks=[
            {
                "chunk_id": "c1",
                "content": "Hello",
                "embedding": [0.1] * 1536,
                "chunk_index": 0,
                "section_path": ["Intro"],
                "anchor": "intro",
                "char_start": 0,
                "char_end": 5,
                "source_type": "firstspirit",
                "document_title": "Doc",
                "document_url": "https://docs.example/d1",
                "document_content_path": None,
            }
        ],
    )
    fake_client.upsert.assert_awaited_once()
    kwargs = fake_client.upsert.await_args.kwargs
    assert kwargs["collection_name"] == vector_store.QDRANT_COLLECTION
    [point] = kwargs["points"]
    assert vector_store.QDRANT_DENSE_VECTOR_NAME in point.vector
    assert vector_store.QDRANT_SPARSE_VECTOR_NAME in point.vector
    assert point.payload["chunk_id"] == "c1"
    assert point.payload["document_title"] == "Doc"


async def test_upsert_chunks_empty_list_is_noop(fake_client):
    await vector_store.upsert_chunks(document_id="d1", chunks=[])
    fake_client.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# hybrid_search — Query API with RRF fusion
# ---------------------------------------------------------------------------


async def test_hybrid_search_uses_query_api_with_rrf_fusion(fake_client):
    fake_client.query_points.return_value = _query_response(
        [
            _scored_point(
                {
                    "chunk_id": "c1",
                    "document_id": "d1",
                    "document_title": "T",
                    "document_url": "u",
                    "content": "body",
                    "chunk_index": 0,
                    "source_type": "firstspirit",
                }
            )
        ]
    )
    out = await vector_store.hybrid_search(
        query_text="hello",
        query_embedding=[0.1] * 1536,
        top_k=5,
    )
    fake_client.query_points.assert_awaited_once()
    kwargs = fake_client.query_points.await_args.kwargs
    # Server-side RRF: prefetch over dense + sparse, fusion=FusionQuery(RRF).
    assert len(kwargs["prefetch"]) == 2
    usings = {p.using for p in kwargs["prefetch"]}
    assert usings == {vector_store.QDRANT_DENSE_VECTOR_NAME, vector_store.QDRANT_SPARSE_VECTOR_NAME}
    # The top-level query is the RRF FusionQuery.
    from qdrant_client import models

    assert isinstance(kwargs["query"], models.FusionQuery)
    assert kwargs["query"].fusion == models.Fusion.RRF
    # Canonical hit shape with metadata from the payload.
    assert out[0]["chunk_id"] == "c1"
    assert out[0]["document_title"] == "T"
    assert out[0]["score"] == 0.42


async def test_hybrid_search_empty_query_raises(fake_client):
    with pytest.raises(ValueError):
        await vector_store.hybrid_search(
            query_text="",
            query_embedding=[0.1] * 1536,
        )


# ---------------------------------------------------------------------------
# keyword_search / semantic_search use the right vector kind
# ---------------------------------------------------------------------------


async def test_keyword_search_uses_sparse_only(fake_client):
    fake_client.query_points.return_value = _query_response([])
    await vector_store.keyword_search("hello", top_k=10)
    kwargs = fake_client.query_points.await_args.kwargs
    assert kwargs["using"] == vector_store.QDRANT_SPARSE_VECTOR_NAME
    # No prefetch — single-vector query.
    assert "prefetch" not in kwargs or not kwargs.get("prefetch")


async def test_semantic_search_uses_dense_only(fake_client):
    fake_client.query_points.return_value = _query_response([])
    await vector_store.semantic_search([0.1] * 1536, top_k=10)
    kwargs = fake_client.query_points.await_args.kwargs
    assert kwargs["using"] == vector_store.QDRANT_DENSE_VECTOR_NAME


# ---------------------------------------------------------------------------
# Source-type filter
# ---------------------------------------------------------------------------


async def test_source_type_filter_applied_when_provided(fake_client):
    fake_client.query_points.return_value = _query_response([])
    await vector_store.hybrid_search(
        query_text="hi",
        query_embedding=[0.0] * 1536,
        top_k=5,
        allowed_source_types=["firstspirit"],
    )
    kwargs = fake_client.query_points.await_args.kwargs
    # Both prefetches must carry the same filter.
    for prefetch in kwargs["prefetch"]:
        assert prefetch.filter is not None


async def test_source_type_filter_none_means_no_filter(fake_client):
    fake_client.query_points.return_value = _query_response([])
    await vector_store.hybrid_search(
        query_text="hi",
        query_embedding=[0.0] * 1536,
        top_k=5,
        allowed_source_types=None,
    )
    kwargs = fake_client.query_points.await_args.kwargs
    for prefetch in kwargs["prefetch"]:
        assert prefetch.filter is None

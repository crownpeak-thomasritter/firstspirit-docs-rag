"""Tests for ``backend.rag.retriever_hybrid`` — the thin shim over Qdrant.

After the pivot the retriever just delegates to ``vector_store.hybrid_search``;
this test pins the public surface (function signature + default
``allowed_source_types``) so callers don't notice the change.
"""

from __future__ import annotations

from backend.config import DEFAULT_SOURCE_TYPE
from backend.rag import retriever_hybrid


async def test_retrieve_hybrid_delegates_with_default_source_types(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_hybrid_search(
        *, query_text, query_embedding, top_k, allowed_source_types
    ) -> list[dict]:
        captured["query_text"] = query_text
        captured["top_k"] = top_k
        captured["allowed_source_types"] = list(allowed_source_types)
        return [{"chunk_id": "c1"}]

    monkeypatch.setattr("backend.rag.vector_store.hybrid_search", fake_hybrid_search, raising=False)

    hits = await retriever_hybrid.retrieve_hybrid(
        "heap tuning",
        [0.1] * 1536,
        top_k=5,
    )
    assert hits == [{"chunk_id": "c1"}]
    assert captured["query_text"] == "heap tuning"
    assert captured["top_k"] == 5
    assert captured["allowed_source_types"] == [DEFAULT_SOURCE_TYPE]


async def test_retrieve_hybrid_passes_through_explicit_source_types(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_hybrid_search(
        *, query_text, query_embedding, top_k, allowed_source_types
    ) -> list[dict]:
        captured["allowed_source_types"] = list(allowed_source_types)
        return []

    monkeypatch.setattr("backend.rag.vector_store.hybrid_search", fake_hybrid_search, raising=False)

    await retriever_hybrid.retrieve_hybrid(
        "q",
        [0.0],
        top_k=3,
        allowed_source_types=["paid"],
    )
    assert captured["allowed_source_types"] == ["paid"]


def test_invalidate_cache_is_a_noop():
    # Should not raise — it's retained as a stable hook for routes/sources.
    retriever_hybrid.invalidate_cache()

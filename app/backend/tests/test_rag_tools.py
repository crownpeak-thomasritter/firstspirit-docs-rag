"""Tests for ``backend.rag.tools``.

Covers the pure dispatch + shaping logic of the LLM retrieval tools without
hitting Postgres. Repository, retriever, embedder and expansion are all
monkeypatched.

What we verify:
    * ``_parse_args`` accepts dicts, strings, blanks, and rejects bad JSON.
    * ``_clamp_top_k`` clamps to ``[1, max]`` and coerces non-numerics.
    * ``_apply_per_document_cap`` respects per-document limits and preserves
      ranking order.
    * ``_normalize_chunk_shape`` always emits the canonical key set.
    * ``_format_search_results`` includes the ``[c:<chunk_id>]`` marker and
      breadcrumb.
    * ``execute_search_hybrid`` wires retriever → cap → normalise → format and
      surfaces failures as ``{"ok": False}``.
    * ``execute_get_document`` enforces the whitelist and produces canonical
      citation-shaped chunks.
    * ``execute_tool`` dispatches by name and returns an error for unknown
      tools.
"""

from __future__ import annotations

import pytest

from backend.rag import tools

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_args_accepts_dict():
    assert tools._parse_args({"query": "x"}) == {"query": "x"}


def test_parse_args_accepts_json_string():
    assert tools._parse_args('{"query": "x"}') == {"query": "x"}


def test_parse_args_blank_string_returns_empty_dict():
    assert tools._parse_args("") == {}
    assert tools._parse_args("   ") == {}


def test_parse_args_invalid_json_returns_none():
    assert tools._parse_args("{not json}") is None


def test_parse_args_non_object_json_returns_none():
    # A JSON array is valid JSON but not a tool-arg dict.
    assert tools._parse_args("[1, 2, 3]") is None


def test_clamp_top_k_default_when_none():
    assert tools._clamp_top_k(None) == 10


def test_clamp_top_k_caps_at_maximum():
    assert tools._clamp_top_k(9999) == 30


def test_clamp_top_k_floor_is_one():
    assert tools._clamp_top_k(0) == 1
    assert tools._clamp_top_k(-5) == 1


def test_clamp_top_k_coerces_string():
    assert tools._clamp_top_k("7") == 7


def test_clamp_top_k_bad_string_returns_default():
    assert tools._clamp_top_k("seven") == 10


def test_apply_per_document_cap_limits_per_document():
    chunks = [
        {"document_id": "a", "chunk_id": "a1"},
        {"document_id": "a", "chunk_id": "a2"},
        {"document_id": "a", "chunk_id": "a3"},  # dropped — over cap
        {"document_id": "b", "chunk_id": "b1"},
        {"document_id": "b", "chunk_id": "b2"},  # dropped — over cap
    ]
    kept = tools._apply_per_document_cap(chunks, max_per_document=1)
    ids = [c["chunk_id"] for c in kept]
    assert ids == ["a1", "b1"]


def test_apply_per_document_cap_zero_is_noop():
    chunks = [{"document_id": "a", "chunk_id": "a1"}]
    assert tools._apply_per_document_cap(chunks, 0) == chunks


def test_apply_per_document_cap_preserves_chunks_without_doc_id():
    chunks = [
        {"chunk_id": "orphan"},
        {"document_id": "a", "chunk_id": "a1"},
        {"document_id": "a", "chunk_id": "a2"},
    ]
    kept = tools._apply_per_document_cap(chunks, max_per_document=1)
    assert [c["chunk_id"] for c in kept] == ["orphan", "a1"]


def test_normalize_chunk_shape_has_canonical_keys():
    minimal: dict = {"chunk_id": "x", "document_id": "d", "content": "y"}
    out = tools._normalize_chunk_shape(minimal)
    assert set(out.keys()) == set(tools._CANONICAL_CHUNK_KEYS)
    assert out["section_path"] == []
    assert out["chunk_index"] == 0
    assert out["document_url"] is None
    assert out["anchor"] is None
    assert out["source_type"] == ""


def test_format_search_results_empty_returns_helpful_message():
    assert "No relevant chunks" in tools._format_search_results([])


def test_format_search_results_emits_citation_marker_and_breadcrumb():
    chunks = [
        {
            "chunk_id": "chunk-1",
            "document_title": "FirstSpirit Module Manual",
            "section_path": ["Installation", "Heap tuning"],
            "content": "Set -Xmx to 4G.",
        }
    ]
    out = tools._format_search_results(chunks)
    assert out.startswith("[c:chunk-1] FirstSpirit Module Manual")
    assert "Installation › Heap tuning" in out
    assert "Set -Xmx to 4G." in out


def test_format_search_results_no_breadcrumb_when_section_path_empty():
    chunks = [
        {
            "chunk_id": "c1",
            "document_title": "Doc",
            "section_path": [],
            "content": "body",
        }
    ]
    out = tools._format_search_results(chunks)
    assert "[c:c1] Doc\n" in out
    assert "›" not in out


# ---------------------------------------------------------------------------
# Executors — hybrid search
# ---------------------------------------------------------------------------


async def test_execute_search_hybrid_happy_path(monkeypatch):
    async def fake_retrieve(query, embedding, top_k, allowed_source_types=None):
        assert query == "heap tuning"
        assert top_k == 5
        return [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "document_title": "Module Manual",
                "section_path": ["Installation", "Heap tuning"],
                "anchor": "heap-tuning",
                "document_url": "https://docs.example/m",
                "content": "Set -Xmx.",
                "chunk_index": 3,
                "source_type": "firstspirit",
            }
        ]

    async def fake_embed(query, cache):
        return [0.0] * 1536

    monkeypatch.setattr(
        "backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve, raising=False
    )
    monkeypatch.setattr(tools, "_embed_query", fake_embed)
    # Disable neighbour expansion so we're testing tools.py in isolation.
    monkeypatch.setattr("backend.config.RETRIEVAL_EXPANSION_WINDOW", 0, raising=False)
    monkeypatch.setattr("backend.config.RETRIEVAL_MAX_PER_DOCUMENT", 0, raising=False)

    result = await tools.execute_search_hybrid({"query": "heap tuning", "top_k": 5})

    assert result["ok"] is True
    assert "[c:chunk-1] Module Manual" in result["text"]
    assert result["chunks"][0]["chunk_id"] == "chunk-1"
    assert result["chunks"][0]["section_path"] == ["Installation", "Heap tuning"]


async def test_execute_search_hybrid_missing_query_returns_error():
    result = await tools.execute_search_hybrid({"top_k": 5})
    assert result["ok"] is False
    assert "missing" in result["error"]


async def test_execute_search_hybrid_invalid_json_returns_error():
    result = await tools.execute_search_hybrid("{not json}")
    assert result["ok"] is False
    assert "invalid JSON" in result["error"]


async def test_execute_search_hybrid_retriever_failure_returns_error(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    async def fake_embed(query, cache):
        return [0.0]

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", boom, raising=False)
    monkeypatch.setattr(tools, "_embed_query", fake_embed)

    result = await tools.execute_search_hybrid({"query": "anything"})

    assert result["ok"] is False
    assert "search failed" in result["error"]


# ---------------------------------------------------------------------------
# Executors — keyword search
# ---------------------------------------------------------------------------


async def test_execute_search_keyword_returns_canonical_chunks(monkeypatch):
    async def fake_keyword_search(query, top_k=10, allowed_source_types=None):
        return [
            {
                "chunk_id": "k1",
                "id": "k1",
                "document_id": "d1",
                "document_title": "Hydrated Doc",
                "document_url": "https://docs.example/d1",
                "document_content_path": None,
                "source_type": "firstspirit",
                "content": "match",
                "section_path": ["A"],
                "anchor": "a",
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 5,
                "score": 0.5,
            }
        ]

    monkeypatch.setattr("backend.rag.vector_store.keyword_search", fake_keyword_search)
    monkeypatch.setattr("backend.config.RETRIEVAL_EXPANSION_WINDOW", 0, raising=False)

    result = await tools.execute_search_keyword({"query": "match"})

    assert result["ok"] is True
    assert result["chunks"][0]["document_title"] == "Hydrated Doc"
    assert result["chunks"][0]["document_url"] == "https://docs.example/d1"


async def test_execute_search_semantic_returns_canonical_chunks(monkeypatch):
    async def fake_embed(query, cache):
        return [0.0] * 1536

    async def fake_semantic_search(embedding, top_k=10, allowed_source_types=None):
        return [
            {
                "chunk_id": "s1",
                "id": "s1",
                "document_id": "d2",
                "document_title": "Semantic Doc",
                "document_url": None,
                "document_content_path": "notes/x.md",
                "source_type": "firstspirit",
                "content": "paraphrase",
                "section_path": [],
                "anchor": None,
                "chunk_index": 1,
                "char_start": 0,
                "char_end": 10,
                "score": 0.9,
            }
        ]

    monkeypatch.setattr(tools, "_embed_query", fake_embed)
    monkeypatch.setattr("backend.rag.vector_store.semantic_search", fake_semantic_search)
    monkeypatch.setattr("backend.config.RETRIEVAL_EXPANSION_WINDOW", 0, raising=False)

    result = await tools.execute_search_semantic({"query": "match"})

    assert result["ok"] is True
    assert result["chunks"][0]["document_title"] == "Semantic Doc"
    assert result["chunks"][0]["document_content_path"] == "notes/x.md"


# ---------------------------------------------------------------------------
# Executors — get_document
# ---------------------------------------------------------------------------


async def test_execute_get_document_rejects_outside_whitelist(monkeypatch):
    result = await tools.execute_get_document(
        {"document_id": "doc-1"},
        document_id_whitelist={"doc-2"},
    )
    assert result["ok"] is False
    assert "not in the current library" in result["error"]


async def test_execute_get_document_missing_param_returns_error():
    result = await tools.execute_get_document({})
    assert result["ok"] is False
    assert "missing required parameter" in result["error"]


async def test_execute_get_document_not_found_returns_error(monkeypatch):
    async def fake_get_document(doc_id):
        return None

    monkeypatch.setattr("backend.db.repository.get_document", fake_get_document)
    result = await tools.execute_get_document({"document_id": "ghost"}, document_id_whitelist=None)
    assert result["ok"] is False
    assert "not found" in result["error"]


async def test_execute_get_document_happy_path(monkeypatch):
    async def fake_get_document(doc_id):
        return {
            "id": "doc-1",
            "title": "FS Manual",
            "url": "https://docs.example/doc-1",
            "content_path": None,
            "source_type": "firstspirit",
        }

    async def fake_list_chunks_for_document(doc_id):
        return [
            {
                "id": "chunk-1",
                "content": "First paragraph.",
                "section_path": ["Intro"],
                "anchor": "intro",
                "chunk_index": 0,
            }
        ]

    monkeypatch.setattr("backend.db.repository.get_document", fake_get_document)
    monkeypatch.setattr(
        "backend.db.repository.list_chunks_for_document", fake_list_chunks_for_document
    )

    result = await tools.execute_get_document({"document_id": "doc-1"})

    assert result["ok"] is True
    assert "# FS Manual" in result["text"]
    assert "[c:chunk-1]" in result["text"]
    assert result["chunks"][0]["chunk_id"] == "chunk-1"
    assert result["chunks"][0]["document_title"] == "FS Manual"
    assert result["chunks"][0]["section_path"] == ["Intro"]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def test_execute_tool_unknown_returns_error():
    result = await tools.execute_tool("destroy_the_db", {})
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


@pytest.mark.parametrize(
    "name",
    [
        "search_documents",
        "keyword_search_documents",
        "semantic_search_documents",
        "get_document",
    ],
)
def test_tool_schemas_expose_canonical_names(name):
    names = {t["function"]["name"] for t in tools.TOOL_SCHEMAS}
    assert name in names


def test_serialize_tool_result_success_returns_text():
    assert tools.serialize_tool_result({"ok": True, "text": "hello"}) == "hello"


def test_serialize_tool_result_failure_includes_error():
    assert "boom" in tools.serialize_tool_result({"ok": False, "error": "boom"})

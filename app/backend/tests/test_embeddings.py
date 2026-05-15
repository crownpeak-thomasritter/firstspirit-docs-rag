"""Tests for ``backend.rag.embeddings``.

Covers the per-request token / item cap splitting in ``embed_batch`` so a
large document doesn't trip OpenAI's 300k-token-per-request limit (the
regression we hit during the first full URL-list sync).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.rag import embeddings


class _FakeData:
    def __init__(self, index: int, dim: int = 1536) -> None:
        self.index = index
        self.embedding = [float(index)] * dim


def _make_fake_client(calls: list[list[str]]) -> MagicMock:
    """Return a client whose embeddings.create records each call and returns
    one vector per input in the same order."""

    def fake_create(model: str, input: list[str]):
        calls.append(list(input))
        return MagicMock(data=[_FakeData(i) for i in range(len(input))])

    client = MagicMock()
    client.embeddings.create.side_effect = fake_create
    return client


def test_embed_batch_returns_empty_for_empty_input():
    assert embeddings.embed_batch([]) == []


def test_embed_batch_rejects_empty_string(monkeypatch):
    monkeypatch.setattr(embeddings, "_get_client", lambda: _make_fake_client([]))
    with pytest.raises(ValueError, match="non-empty"):
        embeddings.embed_batch(["ok", "   "])


def test_embed_batch_splits_when_token_cap_exceeded(monkeypatch):
    """A batch whose total tokens exceed the per-request cap is sent in
    multiple sub-requests."""
    calls: list[list[str]] = []
    monkeypatch.setattr(embeddings, "_get_client", lambda: _make_fake_client(calls))
    # Drop the cap so the test runs fast — 20 tokens per request, every input
    # is exactly 11 tokens ("token " repeated 11 times tokenises to 11).
    monkeypatch.setattr(embeddings, "_MAX_TOKENS_PER_REQUEST", 20)
    monkeypatch.setattr(embeddings, "_MAX_ITEMS_PER_REQUEST", 1024)

    inputs = [f"token-{i} " * 11 for i in range(5)]
    result = embeddings.embed_batch(inputs)

    assert len(result) == 5
    assert len(calls) == 5, "each 11-token input should land in its own sub-request"


def test_embed_batch_splits_when_item_cap_exceeded(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(embeddings, "_get_client", lambda: _make_fake_client(calls))
    monkeypatch.setattr(embeddings, "_MAX_ITEMS_PER_REQUEST", 2)
    monkeypatch.setattr(embeddings, "_MAX_TOKENS_PER_REQUEST", 10_000)

    inputs = ["a", "b", "c", "d", "e"]
    result = embeddings.embed_batch(inputs)

    assert len(result) == 5
    assert [len(c) for c in calls] == [2, 2, 1]


def test_embed_batch_preserves_input_order(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(embeddings, "_get_client", lambda: _make_fake_client(calls))
    monkeypatch.setattr(embeddings, "_MAX_ITEMS_PER_REQUEST", 2)
    monkeypatch.setattr(embeddings, "_MAX_TOKENS_PER_REQUEST", 10_000)

    embeddings.embed_batch(["one", "two", "three"])

    # Verify input order survives sub-batching.
    assert calls == [["one", "two"], ["three"]]


def test_embed_batch_truncates_oversized_input(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(embeddings, "_get_client", lambda: _make_fake_client(calls))
    monkeypatch.setattr(embeddings, "_MAX_TOKENS_PER_INPUT", 5)
    monkeypatch.setattr(embeddings, "_MAX_TOKENS_PER_REQUEST", 1000)

    # 30 tokens of "x " — will be truncated down to 5.
    embeddings.embed_batch(["x " * 30])

    sent = calls[0][0]
    # The truncated string should encode to exactly _MAX_TOKENS_PER_INPUT tokens.
    assert embeddings._count_tokens(sent) == 5

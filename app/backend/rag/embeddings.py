"""
Embeddings service — wraps the configured provider's embeddings API.

The provider (OpenRouter or OpenAI native) is selected by the
``EMBEDDING_PROVIDER`` env var via ``llm.providers.get_sync_embed_client``.
The OpenRouter slug ``openai/text-embedding-3-small`` is automatically
rewritten to the unprefixed form when the provider is OpenAI native.

Exposes:
  embed_text(text: str) -> list[float]
  embed_batch(texts: list[str]) -> list[list[float]]

Default model: openai/text-embedding-3-small (dimensionality: 1536)
"""

from __future__ import annotations

import logging

import tiktoken
from openai import OpenAI

from backend.config import EMBEDDING_MODEL
from backend.llm.providers import get_sync_embed_client, resolve_embedding_model

logger = logging.getLogger(__name__)

# OpenAI's embeddings endpoint enforces two per-request caps:
#   - tokens: 300_000 for `text-embedding-3-*` models
#   - items:  2048 inputs per request
# We stay under both with a safety margin so a single oversized doc (e.g. a
# large PDF) doesn't break the run. The token budget uses the model's own
# tokenizer (cl100k_base for text-embedding-3-*).
_MAX_TOKENS_PER_REQUEST = 250_000
_MAX_ITEMS_PER_REQUEST = 1024
# Per-item cap on the model (8191 for text-embedding-3-small). Chunks above
# this are truncated at the token boundary — the chunker targets 512 tokens,
# so this only ever trips for pathological inputs.
_MAX_TOKENS_PER_INPUT = 8191

_tokenizer = tiktoken.get_encoding("cl100k_base")


def _get_client() -> OpenAI:
    return get_sync_embed_client()


def _count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text, disallowed_special=()))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    tokens = _tokenizer.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text
    return _tokenizer.decode(tokens[:max_tokens])


def _batches_under_caps(texts: list[str]) -> list[list[str]]:
    """Split ``texts`` into sub-batches that fit OpenAI's per-request caps.

    Each sub-batch obeys ``_MAX_TOKENS_PER_REQUEST`` and
    ``_MAX_ITEMS_PER_REQUEST``. Items longer than ``_MAX_TOKENS_PER_INPUT``
    are truncated at the token boundary so a single oversized chunk doesn't
    break a whole document's ingestion.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for text in texts:
        prepared = text
        n = _count_tokens(prepared)
        if n > _MAX_TOKENS_PER_INPUT:
            logger.warning(
                "Embedding input has %d tokens (> %d); truncating.", n, _MAX_TOKENS_PER_INPUT
            )
            prepared = _truncate_to_tokens(prepared, _MAX_TOKENS_PER_INPUT)
            n = _MAX_TOKENS_PER_INPUT
        if current and (
            current_tokens + n > _MAX_TOKENS_PER_REQUEST or len(current) >= _MAX_ITEMS_PER_REQUEST
        ):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(prepared)
        current_tokens += n
    if current:
        batches.append(current)
    return batches


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed_text(text: str) -> list[float]:
    """
    Embed a single text string via the active embedding provider.

    Args:
        text: A non-empty string to embed.

    Returns:
        A list of 1536 floats representing the embedding vector.

    Raises:
        ValueError: If *text* is empty or whitespace-only.
        Exception: If the API call fails.
    """
    if not text or not text.strip():
        raise ValueError(
            "embed_text() requires a non-empty string; got an empty or whitespace-only value."
        )

    client = _get_client()
    try:
        response = client.embeddings.create(
            model=resolve_embedding_model(EMBEDDING_MODEL),
            input=text,
        )
    except Exception as exc:
        logger.error("Embeddings API call failed: %s", exc)
        raise RuntimeError(f"Embeddings API request failed: {exc}") from exc

    embedding = response.data[0].embedding
    return list(embedding)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of text strings via the active embedding provider, splitting
    into sub-requests to stay under the per-request token and item caps.

    Args:
        texts: A list of strings. May be empty (returns [] immediately).

    Returns:
        A list of embedding vectors, one per input text, in the same order.
        Returns [] if *texts* is empty without making any API call.

    Raises:
        ValueError: If any text in the list is empty or whitespace-only.
        RuntimeError: If any sub-request fails.
    """
    if not texts:
        return []

    for i, text in enumerate(texts):
        if not text or not text.strip():
            raise ValueError(
                f"embed_batch() requires all texts to be non-empty; got empty string at index {i}."
            )

    client = _get_client()
    model = resolve_embedding_model(EMBEDDING_MODEL)
    sub_batches = _batches_under_caps(texts)

    out: list[list[float]] = []
    for batch_idx, batch in enumerate(sub_batches):
        try:
            response = client.embeddings.create(model=model, input=batch)
        except Exception as exc:
            logger.error(
                "Embeddings batch API call failed (sub-batch %d/%d, %d items): %s",
                batch_idx + 1,
                len(sub_batches),
                len(batch),
                exc,
            )
            raise RuntimeError(f"Embeddings batch API request failed: {exc}") from exc

        sorted_data = sorted(response.data, key=lambda d: d.index)
        out.extend(list(d.embedding) for d in sorted_data)

    return out

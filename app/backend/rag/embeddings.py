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

from openai import OpenAI

from backend.config import EMBEDDING_MODEL
from backend.llm.providers import get_sync_embed_client, resolve_embedding_model

logger = logging.getLogger(__name__)


def _get_client() -> OpenAI:
    return get_sync_embed_client()


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
    Embed a list of text strings via the active embedding provider in a single batched API call.

    Args:
        texts: A list of strings. May be empty (returns [] immediately).

    Returns:
        A list of embedding vectors, one per input text, in the same order.
        Returns [] if *texts* is empty without making any API call.

    Raises:
        ValueError: If any text in the list is empty or whitespace-only.
        Exception: If the API call fails.
    """
    if not texts:
        return []

    # Validate all texts before making the API call
    for i, text in enumerate(texts):
        if not text or not text.strip():
            raise ValueError(
                f"embed_batch() requires all texts to be non-empty; got empty string at index {i}."
            )

    client = _get_client()
    try:
        response = client.embeddings.create(
            model=resolve_embedding_model(EMBEDDING_MODEL),
            input=texts,
        )
    except Exception as exc:
        logger.error("Embeddings batch API call failed: %s", exc)
        raise RuntimeError(f"Embeddings batch API request failed: {exc}") from exc

    # The API guarantees results in the same order as inputs
    # but we sort by index just to be safe
    sorted_data = sorted(response.data, key=lambda d: d.index)
    return [list(d.embedding) for d in sorted_data]

"""LLM provider factory.

Returns an OpenAI-SDK-shaped client configured for the active provider —
either OpenRouter (default) or OpenAI native. Both share the OpenAI wire
protocol, so swapping is just ``base_url`` + ``api_key``.

Chat completions go through ``AsyncOpenAI``; embedding generation in
``rag/embeddings.py`` is synchronous and uses ``OpenAI``. Both clients are
singletons cached at module level.

The ``is_openrouter_chat()`` helper gates Anthropic-specific request shape
(notably the ``cache_control: {"type": "ephemeral"}`` block) — OpenAI native
rejects extra keys with HTTP 400.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI, OpenAI

from backend.config import (
    EMBEDDING_PROVIDER,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)

logger = logging.getLogger(__name__)

_async_chat_client: AsyncOpenAI | None = None
_sync_embed_client: OpenAI | None = None


def get_async_chat_client() -> AsyncOpenAI:
    """Return the singleton async chat client for the configured LLM provider."""
    global _async_chat_client
    if _async_chat_client is None:
        if LLM_PROVIDER == "openai":
            if not OPENAI_API_KEY:
                raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
            _async_chat_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        else:
            if not OPENROUTER_API_KEY:
                raise RuntimeError("LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set.")
            _async_chat_client = AsyncOpenAI(
                api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL
            )
    return _async_chat_client


def get_sync_embed_client() -> OpenAI:
    """Return the singleton sync embed client for the configured embedding provider."""
    global _sync_embed_client
    if _sync_embed_client is None:
        if EMBEDDING_PROVIDER == "openai":
            if not OPENAI_API_KEY:
                raise RuntimeError("EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set.")
            _sync_embed_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        else:
            if not OPENROUTER_API_KEY:
                raise RuntimeError(
                    "EMBEDDING_PROVIDER=openrouter but OPENROUTER_API_KEY is not set."
                )
            _sync_embed_client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    return _sync_embed_client


def resolve_embedding_model(model: str) -> str:
    """OpenRouter uses ``openai/text-embedding-3-small``; OpenAI native wants the
    unprefixed form. Strip the ``openai/`` prefix only when the active
    embedding provider is OpenAI native.
    """
    if EMBEDDING_PROVIDER == "openai" and model.startswith("openai/"):
        return model[len("openai/") :]
    return model


def is_openrouter_chat() -> bool:
    """True when the chat path goes via OpenRouter (Anthropic cache_control etc.)."""
    return LLM_PROVIDER != "openai"


def reset_clients() -> None:
    """Drop cached clients — used by tests after env-var mutation."""
    global _async_chat_client, _sync_embed_client
    _async_chat_client = None
    _sync_embed_client = None

"""Tests for ``backend.llm.providers``.

Verifies the factory builds clients configured for the right provider and
that ``resolve_embedding_model`` strips the ``openai/`` prefix only for the
OpenAI native path.
"""

from __future__ import annotations

import importlib

import pytest

from backend import config
from backend.llm import providers


def _reload_providers_with(monkeypatch, **env: str):
    """Reload config + providers after env mutation so the factory sees the new values."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config)
    importlib.reload(providers)
    return providers


def _restore_providers(monkeypatch):
    """Reload back to defaults after a test mutates env vars."""
    importlib.reload(config)
    importlib.reload(providers)


def test_get_async_chat_client_openrouter(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        LLM_PROVIDER="openrouter",
        OPENROUTER_API_KEY="or-test",
        OPENAI_API_KEY="oa-test",
    )
    try:
        client = mod.get_async_chat_client()
        assert str(client.base_url).startswith("https://openrouter.ai")
    finally:
        _restore_providers(monkeypatch)


def test_get_async_chat_client_openai_native(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="oa-test",
        OPENROUTER_API_KEY="or-test",
    )
    try:
        client = mod.get_async_chat_client()
        assert "openai.com" in str(client.base_url)
    finally:
        _restore_providers(monkeypatch)


def test_get_sync_embed_client_openrouter(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        EMBEDDING_PROVIDER="openrouter",
        OPENROUTER_API_KEY="or-test",
        OPENAI_API_KEY="oa-test",
    )
    try:
        client = mod.get_sync_embed_client()
        assert str(client.base_url).startswith("https://openrouter.ai")
    finally:
        _restore_providers(monkeypatch)


def test_get_sync_embed_client_openai_native(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        EMBEDDING_PROVIDER="openai",
        OPENAI_API_KEY="oa-test",
        OPENROUTER_API_KEY="or-test",
    )
    try:
        client = mod.get_sync_embed_client()
        assert "openai.com" in str(client.base_url)
    finally:
        _restore_providers(monkeypatch)


def test_resolve_embedding_model_openrouter_keeps_slug(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        EMBEDDING_PROVIDER="openrouter",
        OPENROUTER_API_KEY="or-test",
        OPENAI_API_KEY="oa-test",
    )
    try:
        assert (
            mod.resolve_embedding_model("openai/text-embedding-3-small")
            == "openai/text-embedding-3-small"
        )
    finally:
        _restore_providers(monkeypatch)


def test_resolve_embedding_model_openai_strips_prefix(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        EMBEDDING_PROVIDER="openai",
        OPENAI_API_KEY="oa-test",
        OPENROUTER_API_KEY="or-test",
    )
    try:
        assert (
            mod.resolve_embedding_model("openai/text-embedding-3-small") == "text-embedding-3-small"
        )
    finally:
        _restore_providers(monkeypatch)


def test_is_openrouter_chat_reflects_env(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="oa-test",
        OPENROUTER_API_KEY="or-test",
    )
    try:
        assert mod.is_openrouter_chat() is False
    finally:
        _restore_providers(monkeypatch)
    mod = _reload_providers_with(
        monkeypatch,
        LLM_PROVIDER="openrouter",
        OPENAI_API_KEY="oa-test",
        OPENROUTER_API_KEY="or-test",
    )
    try:
        assert mod.is_openrouter_chat() is True
    finally:
        _restore_providers(monkeypatch)


def test_get_async_chat_client_missing_key_raises(monkeypatch):
    mod = _reload_providers_with(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="",
        OPENROUTER_API_KEY="",
    )
    try:
        with pytest.raises(RuntimeError):
            mod.get_async_chat_client()
    finally:
        _restore_providers(monkeypatch)

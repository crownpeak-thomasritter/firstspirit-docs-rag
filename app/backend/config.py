"""
Configuration module for the FirstSpirit Docs RAG backend.

Loads environment variables from a project-root .env file if present. All env
reads happen here; downstream modules import constants, never os.environ.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _find_and_load_env() -> None:
    """Search parent directories for .env and load it; tolerate missing file."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            logger.info("Loaded .env from %s", candidate)
            return
    logger.info("No .env on disk; assuming env vars are injected.")


_find_and_load_env()


# ---------------------------------------------------------------------------
# LLM / embeddings (provider-agnostic — OpenRouter or OpenAI native)
# ---------------------------------------------------------------------------

# Provider selection. Values: "openrouter" | "openai".
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "openrouter").strip().lower()
EMBEDDING_PROVIDER: str = os.environ.get("EMBEDDING_PROVIDER", "openrouter").strip().lower()

OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

_active_chat_key_missing = (LLM_PROVIDER == "openrouter" and not OPENROUTER_API_KEY) or (
    LLM_PROVIDER == "openai" and not OPENAI_API_KEY
)
_active_embed_key_missing = (EMBEDDING_PROVIDER == "openrouter" and not OPENROUTER_API_KEY) or (
    EMBEDDING_PROVIDER == "openai" and not OPENAI_API_KEY
)
if _active_chat_key_missing or _active_embed_key_missing:
    print(
        "WARNING: API key for the active provider is not set. "
        "Embedding and/or LLM features will not work.",
        file=sys.stderr,
    )

# Default embedding model uses the OpenRouter slug (`openai/text-embedding-3-small`);
# the providers factory strips the `openai/` prefix when EMBEDDING_PROVIDER=openai.
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-small")
CHAT_MODEL: str = os.environ.get("CHAT_MODEL", "anthropic/claude-sonnet-4.6")
LLM_REASONING_EFFORT: str = os.environ.get("LLM_REASONING_EFFORT", "").strip().lower()


# ---------------------------------------------------------------------------
# Persistence (SQLite via aiosqlite)
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
# We don't raise at import — tests and the chunker modules are usable without
# a DB. main.py refuses to start without DATABASE_URL.

JWT_SECRET: str = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_SECONDS: int = 7 * 24 * 60 * 60


# ---------------------------------------------------------------------------
# Vector store (Qdrant Cloud)
# ---------------------------------------------------------------------------

QDRANT_URL: str = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")
QDRANT_COLLECTION: str = os.environ.get("QDRANT_COLLECTION", "firstspirit_docs")
QDRANT_DENSE_VECTOR_NAME: str = "dense"
QDRANT_SPARSE_VECTOR_NAME: str = "bm25"
QDRANT_BM25_MODEL: str = os.environ.get("QDRANT_BM25_MODEL", "Qdrant/bm25")
EMBEDDING_DIM: int = 1536


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------

RETRIEVAL_TOP_K: int = 5
HYBRID_CHUNKER_MAX_TOKENS: int = 512
RETRIEVAL_EXPANSION_WINDOW: int = int(os.environ.get("RETRIEVAL_EXPANSION_WINDOW", "1"))

HYBRID_K_CONSTANT: int = 60
HYBRID_OVERFETCH_FACTOR: int = 2
KEYWORD_LANGUAGE: str = "english"

# Per-document diversity cap (renamed from RETRIEVAL_MAX_PER_VIDEO).
RETRIEVAL_MAX_PER_DOCUMENT: int = int(os.environ.get("RETRIEVAL_MAX_PER_DOCUMENT", "3"))

CITATIONS_MAX_COUNT: int = int(os.environ.get("CITATIONS_MAX_COUNT", "10"))

LLM_TOOLS_ENABLED: bool = os.environ.get("LLM_TOOLS_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LLM_TOOLS_MAX_PER_TURN: int = int(os.environ.get("LLM_TOOLS_MAX_PER_TURN", "6"))

CATALOG_ENABLED: bool = os.environ.get("CATALOG_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
CATALOG_TIER: str = os.environ.get("CATALOG_TIER", "standard").strip().lower()
CATALOG_CACHE_TTL_SECONDS: int = int(os.environ.get("CATALOG_CACHE_TTL_SECONDS", "3600"))

# Cap on chars returned by the get_document tool (renamed from TRANSCRIPT_TOOL_MAX_CHARS).
DOCUMENT_TOOL_MAX_CHARS: int = int(os.environ.get("DOCUMENT_TOOL_MAX_CHARS", "120000"))


# ---------------------------------------------------------------------------
# Source ingestion (FirstSpirit docs corpus)
# ---------------------------------------------------------------------------

# Path to a markdown file containing one URL per line (the seed corpus).
# Lines starting with '#' or empty lines are ignored by the ingester.
SOURCE_URL_LIST_PATH: str = os.environ.get("SOURCE_URL_LIST_PATH", "./URL List.md")

# Path to a directory of markdown files (the Obsidian vault). Empty = disabled.
SOURCE_VAULT_PATH: str = os.environ.get("SOURCE_VAULT_PATH", "")

# Polite-crawl identity. Always include a contact URL/email so site owners can
# reach the operator if the crawler misbehaves.
CRAWLER_USER_AGENT: str = os.environ.get(
    "CRAWLER_USER_AGENT",
    "FirstSpiritDocsRAG/1.0 (+contact: claudemax.ps@crownpeak.com)",
)
CRAWLER_REQUEST_DELAY_MS: int = int(os.environ.get("CRAWLER_REQUEST_DELAY_MS", "500"))
CRAWLER_MAX_RETRIES: int = int(os.environ.get("CRAWLER_MAX_RETRIES", "4"))
CRAWLER_TIMEOUT_SECONDS: float = float(os.environ.get("CRAWLER_TIMEOUT_SECONDS", "30.0"))

# Source-type discriminator. Single tier in the MVP — see plan §NOT_BUILDING.
DEFAULT_SOURCE_TYPE: str = os.environ.get("DEFAULT_SOURCE_TYPE", "firstspirit")


# ---------------------------------------------------------------------------
# HTTP / CORS
# ---------------------------------------------------------------------------

BACKEND_PORT: int = 8000
FRONTEND_PORT: int = 5173

_cors_raw: str = os.environ.get(
    "CORS_ORIGINS",
    f"http://localhost:{FRONTEND_PORT},http://127.0.0.1:{FRONTEND_PORT}",
)
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]

FRONTEND_DIST: str = os.environ.get("FRONTEND_DIST", "")

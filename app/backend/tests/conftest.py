"""Shared pytest fixtures for firstspirit-docs-rag backend tests.

Sets env vars required by ``backend.config`` BEFORE the first backend import
so the module doesn't print spurious warnings during collection.
"""

from __future__ import annotations

import os

# Must happen before any ``import backend.*`` — config.py reads these at module
# import time and emits warnings to stderr if they're missing.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("QDRANT_URL", "http://qdrant.test")
os.environ.setdefault("QDRANT_API_KEY", "test-qdrant-key")
os.environ.setdefault("LLM_PROVIDER", "openrouter")
os.environ.setdefault("EMBEDDING_PROVIDER", "openrouter")
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
# Feedback → GitHub: enable the feature in tests so route handlers wire in
# end-to-end. Real GitHub calls are mocked at the route boundary.
os.environ.setdefault("FEEDBACK_ENABLED", "true")
os.environ.setdefault("FEEDBACK_GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("FEEDBACK_GITHUB_REPO", "test-owner/test-repo")
# Skip the polite-crawl sleep in tests.
os.environ["CRAWLER_REQUEST_DELAY_MS"] = "0"

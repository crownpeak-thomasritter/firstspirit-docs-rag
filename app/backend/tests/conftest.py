"""Shared pytest fixtures for firstspirit-docs-rag backend tests.

Sets env vars required by ``backend.config`` BEFORE the first backend import
so the module doesn't print spurious warnings during collection.
"""

from __future__ import annotations

import os

# Must happen before any ``import backend.*`` — config.py reads these at module
# import time and emits warnings to stderr if they're missing.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
# Skip the polite-crawl sleep in tests.
os.environ["CRAWLER_REQUEST_DELAY_MS"] = "0"

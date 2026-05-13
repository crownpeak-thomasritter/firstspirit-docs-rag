"""
Async SQLite connection module.

Per-call connections (no pool) because the workload is single-tenant and
low-QPS; aiosqlite open-on-each-call is ~1ms for a local file and the chat
workload is dominated by LLM streaming. Public surface mirrors the prior
``db/postgres.py`` for callers: ``init_*``, ``close_*``, ``get_*``,
``_acquire()``.

``_acquire()`` is the primary entry point used by ``db/repository.py``. It
yields an ``aiosqlite.Connection`` with ``foreign_keys`` enabled and
``row_factory`` set to ``aiosqlite.Row`` so ``dict(row)`` works downstream.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from backend.config import DATABASE_URL

logger = logging.getLogger(__name__)

_db_path: str | None = None


def _normalise_database_url(url: str) -> str:
    """Strip the SQLAlchemy dialect prefix and return a filesystem path.

    Accepts ``sqlite:///./data/app.db``, ``sqlite+aiosqlite:///./data/app.db``,
    ``sqlite+aiosqlite:////absolute/path``, ``sqlite+aiosqlite:///:memory:``.
    """
    if not url:
        raise RuntimeError("DATABASE_URL is not set; cannot initialise SQLite.")
    for prefix in ("sqlite+aiosqlite:///", "sqlite+pysqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            tail = url[len(prefix) :]
            # `:memory:` literal stays as-is.
            if tail == ":memory:" or tail.startswith(":memory:"):
                return ":memory:"
            # Triple-slash absolute paths: prefix leaves a leading "/" already.
            return tail
    # Bare path: accept as-is.
    return url


async def init_sqlite_db() -> None:
    """Resolve the DB path and ensure the parent directory exists. Idempotent."""
    global _db_path
    if _db_path is not None:
        return
    _db_path = _normalise_database_url(DATABASE_URL)
    if _db_path != ":memory:":
        parent = os.path.dirname(_db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    logger.info("SQLite path resolved: %s", _db_path)


async def close_sqlite_db() -> None:
    """Reset the resolved path. No connection pool to close."""
    global _db_path
    _db_path = None


def get_db_path() -> str:
    if _db_path is None:
        raise RuntimeError(
            "SQLite is not initialised. Call init_sqlite_db() in the FastAPI "
            "lifespan before using any SQLite-backed repository."
        )
    return _db_path


@asynccontextmanager
async def _acquire() -> AsyncIterator[aiosqlite.Connection]:
    """Yield a fresh aiosqlite connection with FK enforcement and Row factory.

    Auto-commits on clean exit; rolls back on exception. Mirrors asyncpg's
    implicit-transaction discipline so callers don't have to remember
    ``await conn.commit()`` after every write.
    """
    path = get_db_path()
    conn = await aiosqlite.connect(path)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise
        else:
            await conn.commit()
    finally:
        await conn.close()

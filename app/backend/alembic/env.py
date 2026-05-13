"""
Alembic environment for FirstSpirit Docs RAG.

Uses a synchronous SQLAlchemy engine for migrations. The runtime application
talks to SQLite via ``aiosqlite`` directly; migrations don't need async — the
sync engine simplifies the env script and avoids needing event-loop handling
inside the Alembic process.

The normalisation step accepts ``sqlite+aiosqlite://`` (the runtime URL form)
and rewrites it to ``sqlite+pysqlite://`` so the sync engine can connect.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model's MetaData object, used by `autogenerate` support.
# We don't use autogenerate (lift-and-shift migration), so target_metadata
# is left empty.
target_metadata: dict | None = None


def get_database_url() -> str:
    """Resolve DATABASE_URL, preferring the environment over the ini value.

    alembic.ini ships with `sqlalchemy.url = env(DATABASE_URL)`, but Alembic
    does NOT natively expand `env(...)` — it returns the literal string,
    which SQLAlchemy then fails to parse. So: read os.environ first, and
    only fall back to the ini if the env var is unset AND the ini value
    looks like a real URL (not the unresolved `env(...)` placeholder).
    """
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    ini_url = config.get_main_option("sqlalchemy.url", "")
    if ini_url and not ini_url.startswith("env("):
        return ini_url
    return ""


def _normalise_url_for_sync(url: str) -> str:
    """Rewrite async-driver URLs to their sync counterparts.

    Migrations run via a sync SQLAlchemy engine (``pysqlite`` for SQLite,
    ``psycopg2`` for Postgres — the latter is only used by the one-shot
    ``scripts/migrate_pg_to_qdrant.py`` import path).
    """
    if url.startswith("sqlite+aiosqlite://"):
        return "sqlite+pysqlite://" + url[len("sqlite+aiosqlite://") :]
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,  # type: ignore[arg-type]
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Entry point for 'alembic upgrade head' when running online."""
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Cannot run Alembic migrations.")
    sync_url = _normalise_url_for_sync(url)
    engine = create_engine(sync_url, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        do_run_migrations(connection)


def run_migrations_offline() -> None:
    raise NotImplementedError(
        "Offline migration mode is not supported. "
        "DATABASE_URL is required; all migrations run online via a sync engine."
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

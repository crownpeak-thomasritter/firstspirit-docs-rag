"""Route tests for :mod:`backend.routes.conversations`.

Focused on the surface that the feedback feature added — ``feedback_enabled``
at the conversation root and ``feedback_submitted`` per-message — using
the same on-disk SQLite + ASGI transport pattern as
``test_routes_feedback.py``.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.db import repository
from backend.db import sqlite as sqlite_mod
from backend.routes import conversations as conversations_route


async def _apply_initial_schema() -> None:
    async with aiosqlite.connect(sqlite_mod.get_db_path()) as conn:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Conversation',
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                sources TEXT,
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
            );
            CREATE TABLE IF NOT EXISTS feedback_submissions (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                suggested_correction TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                github_issue_url TEXT,
                status TEXT NOT NULL DEFAULT 'submitted'
                    CHECK (status IN ('submitted', 'issue_filed', 'issue_failed')),
                created_at TEXT NOT NULL
            );
            """
        )
        await conn.commit()


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(sqlite_mod, "_db_path", None, raising=False)
    monkeypatch.setattr(
        "backend.config.DATABASE_URL",
        f"sqlite+aiosqlite:///{db_path}",
        raising=False,
    )
    import importlib

    importlib.reload(sqlite_mod)
    importlib.reload(repository)
    importlib.reload(conversations_route)
    await sqlite_mod.init_sqlite_db()
    await _apply_initial_schema()
    yield
    await sqlite_mod.close_sqlite_db()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(conversations_route.router, prefix="/api")
    return app


async def test_get_conversation_includes_feedback_enabled_flag(fresh_db):
    conv = await repository.create_conversation(
        user_id=conversations_route.DEFAULT_USER_ID, title="C"
    )

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/conversations/{conv['id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["feedback_enabled"] is True


async def test_get_conversation_feedback_enabled_false_when_token_empty(monkeypatch, fresh_db):
    conv = await repository.create_conversation(
        user_id=conversations_route.DEFAULT_USER_ID, title="C"
    )
    monkeypatch.setattr(conversations_route, "FEEDBACK_GITHUB_TOKEN", "")

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/conversations/{conv['id']}")

    assert resp.status_code == 200
    assert resp.json()["feedback_enabled"] is False


async def test_get_conversation_feedback_enabled_false_when_flag_off(monkeypatch, fresh_db):
    conv = await repository.create_conversation(
        user_id=conversations_route.DEFAULT_USER_ID, title="C"
    )
    monkeypatch.setattr(conversations_route, "FEEDBACK_ENABLED", False)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/conversations/{conv['id']}")

    assert resp.status_code == 200
    assert resp.json()["feedback_enabled"] is False


async def test_get_conversation_messages_have_feedback_submitted_flag(fresh_db):
    conv = await repository.create_conversation(
        user_id=conversations_route.DEFAULT_USER_ID, title="C"
    )
    user_msg = await repository.create_message(
        conversation_id=conv["id"],
        user_id=conversations_route.DEFAULT_USER_ID,
        role="user",
        content="q?",
    )
    assert user_msg is not None
    asst_msg = await repository.create_message(
        conversation_id=conv["id"],
        user_id=conversations_route.DEFAULT_USER_ID,
        role="assistant",
        content="a.",
    )
    assert asst_msg is not None

    # Seed a feedback row for the assistant message.
    feedback = await repository.create_feedback(
        message_id=asst_msg["id"],
        conversation_id=conv["id"],
        user_id=conversations_route.DEFAULT_USER_ID,
        suggested_correction="The right answer is X.",
        payload_json="{}",
    )
    assert feedback is not None

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/conversations/{conv['id']}")

    assert resp.status_code == 200
    body = resp.json()
    flags = {m["id"]: m["feedback_submitted"] for m in body["messages"]}
    assert flags[asst_msg["id"]] is True
    assert flags[user_msg["id"]] is False

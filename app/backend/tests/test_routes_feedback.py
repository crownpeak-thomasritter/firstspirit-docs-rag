"""Route tests for :mod:`backend.routes.feedback`.

Uses ``ASGITransport`` to drive the FastAPI router and the on-disk SQLite
DB the ``fresh_db`` fixture sets up. The GitHub service is mocked at the
route-module boundary so no real network call is made.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.db import repository
from backend.db import sqlite as sqlite_mod
from backend.routes import feedback as feedback_route

# ---------------------------------------------------------------------------
# Local copy of the schema-applying fixture from test_repository_sqlite.py.
# The route tests need the same on-disk SQLite + applied schema, so we
# re-declare here rather than promoting the fixture to conftest.py (a
# scope-limited PR change keeps the diff small).
# ---------------------------------------------------------------------------


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
    # The route module imports the repository alias at import time, so reload
    # it too to pick up the freshly-bound module reference.
    importlib.reload(feedback_route)
    await sqlite_mod.init_sqlite_db()
    await _apply_initial_schema()
    yield
    await sqlite_mod.close_sqlite_db()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(feedback_route.router, prefix="/api")
    return app


async def _seed_conv_user_asst(
    user_id: str = "default-user",
    sources: list[dict] | None = None,
) -> tuple[dict, dict, dict]:
    conv = await repository.create_conversation(user_id=user_id, title="C")
    user_msg = await repository.create_message(
        conversation_id=conv["id"],
        user_id=user_id,
        role="user",
        content="What is the JVM heap setting?",
    )
    assert user_msg is not None
    asst_msg = await repository.create_message(
        conversation_id=conv["id"],
        user_id=user_id,
        role="assistant",
        content="Use -Xmx4g.",
        sources=sources,
    )
    assert asst_msg is not None
    return conv, user_msg, asst_msg


# ---------------------------------------------------------------------------
# Happy path + error mappings
# ---------------------------------------------------------------------------


async def test_post_feedback_files_issue_and_returns_record(monkeypatch, fresh_db):
    sources = [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "document_title": "Heap tuning",
            "document_url": "https://docs.example/heap",
            "document_content_path": None,
            "section_path": [],
            "anchor": None,
            "content": "snippet",
            "chunk_index": 0,
            "source_type": "firstspirit",
            "is_cited": True,
        }
    ]
    _conv, _user, asst = await _seed_conv_user_asst(sources=sources)

    captured: dict = {}

    async def fake_create_issue(**kwargs) -> str:
        captured.update(kwargs)
        return "https://github.com/test-owner/test-repo/issues/42"

    monkeypatch.setattr(feedback_route.github, "create_issue", fake_create_issue)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={
                "message_id": asst["id"],
                "suggested_correction": "The correct answer is to use 8g for prod.",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "issue_filed"
    assert body["github_issue_url"].endswith("/issues/42")

    # Issue body must carry the question + correction; title must include
    # "Answer feedback:" prefix.
    assert captured["repo"] == "test-owner/test-repo"
    assert captured["labels"] == ["feedback"]
    assert "Answer feedback:" in captured["title"]
    assert "JVM heap" in captured["title"]
    assert "The correct answer is to use 8g for prod." in captured["body"]
    assert "Heap tuning" in captured["body"]


async def test_post_feedback_persists_payload_json_snapshot(monkeypatch, fresh_db):
    sources = [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "document_title": "Heap tuning",
            "document_url": "https://docs.example/heap",
            "document_content_path": None,
            "section_path": [],
            "anchor": None,
            "content": "snippet",
            "chunk_index": 0,
            "source_type": "firstspirit",
            "is_cited": True,
        }
    ]
    _conv, _user, asst = await _seed_conv_user_asst(sources=sources)

    async def fake_create_issue(**kwargs) -> str:
        return "https://github.com/x/y/issues/1"

    monkeypatch.setattr(feedback_route.github, "create_issue", fake_create_issue)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={
                "message_id": asst["id"],
                "suggested_correction": "Correction text long enough.",
            },
        )

    assert resp.status_code == 200
    row = await repository.get_feedback_by_id(resp.json()["id"])
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["question"] == "What is the JVM heap setting?"
    assert payload["answer"] == "Use -Xmx4g."
    assert payload["citations"] == [
        {"title": "Heap tuning", "url": "https://docs.example/heap", "content_path": None}
    ]


async def test_post_feedback_503_when_disabled(monkeypatch, fresh_db):
    _conv, _user, asst = await _seed_conv_user_asst()
    monkeypatch.setattr(feedback_route, "FEEDBACK_ENABLED", False)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={"message_id": asst["id"], "suggested_correction": "long enough text."},
        )

    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


async def test_post_feedback_503_when_token_empty(monkeypatch, fresh_db):
    _conv, _user, asst = await _seed_conv_user_asst()
    monkeypatch.setattr(feedback_route, "FEEDBACK_GITHUB_TOKEN", "")

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={"message_id": asst["id"], "suggested_correction": "long enough text."},
        )

    assert resp.status_code == 503


async def test_post_feedback_404_for_unknown_message_id(monkeypatch, fresh_db):
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={
                "message_id": "does-not-exist",
                "suggested_correction": "long enough text.",
            },
        )
    assert resp.status_code == 404


async def test_post_feedback_400_when_message_is_user_role(monkeypatch, fresh_db):
    conv, user_msg, _asst = await _seed_conv_user_asst()

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={
                "message_id": user_msg["id"],
                "suggested_correction": "long enough text.",
            },
        )
    assert resp.status_code == 400
    assert "user message" in resp.json()["detail"].lower()


async def test_post_feedback_validation_rejects_short_correction(monkeypatch, fresh_db):
    _conv, _user, asst = await _seed_conv_user_asst()

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={"message_id": asst["id"], "suggested_correction": "too short"},
        )
    assert resp.status_code == 422


async def test_post_feedback_validation_rejects_whitespace_only_correction(monkeypatch, fresh_db):
    _conv, _user, asst = await _seed_conv_user_asst()

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={"message_id": asst["id"], "suggested_correction": "               "},
        )
    assert resp.status_code == 422


async def test_post_feedback_502_on_github_auth_failure(monkeypatch, fresh_db):
    _conv, _user, asst = await _seed_conv_user_asst()

    async def fake_create_issue(**kwargs):
        raise feedback_route.GitHubAuthError("Bad credentials")

    monkeypatch.setattr(feedback_route.github, "create_issue", fake_create_issue)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={
                "message_id": asst["id"],
                "suggested_correction": "long enough text here.",
            },
        )

    assert resp.status_code == 502
    assert "authentication" in resp.json()["detail"].lower()

    # The feedback row must still exist, in status="issue_failed".
    row = await repository.get_feedback_for_message(asst["id"])
    assert row is not None
    assert row["status"] == "issue_failed"
    assert row["github_issue_url"] is None


async def test_post_feedback_502_on_other_github_failure(monkeypatch, fresh_db):
    _conv, _user, asst = await _seed_conv_user_asst()

    async def fake_create_issue(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(feedback_route.github, "create_issue", fake_create_issue)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/feedback",
            json={
                "message_id": asst["id"],
                "suggested_correction": "long enough text here.",
            },
        )

    assert resp.status_code == 502
    row = await repository.get_feedback_for_message(asst["id"])
    assert row is not None
    assert row["status"] == "issue_failed"

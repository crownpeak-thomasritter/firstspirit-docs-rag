"""Tests for ``backend.routes.sources``.

Exercises the FastAPI router with the ingest functions and repository
monkeypatched. We're verifying the route shapes — wiring, error mapping,
and response models — not the ingest pipelines (those have their own tests).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.routes import sources


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(sources.router, prefix="/api")
    return app


def _summary(kind: str = "url_list") -> dict:
    return {
        "sync_run_id": "run-1",
        "kind": kind,
        "status": "completed",
        "items_total": 1,
        "items_new": 1,
        "items_updated": 0,
        "items_unchanged": 0,
        "items_error": 0,
    }


# ---------------------------------------------------------------------------
# POST /api/sources/sync
# ---------------------------------------------------------------------------


async def test_post_sync_url_list_dispatches_to_url_list(monkeypatch):
    called = {"url_list": 0, "vault": 0}

    async def fake_sync_url_list():
        called["url_list"] += 1
        return _summary("url_list")

    async def fake_sync_vault():
        called["vault"] += 1
        return _summary("vault")

    monkeypatch.setattr(sources, "sync_url_list", fake_sync_url_list)
    monkeypatch.setattr(sources, "sync_vault", fake_sync_vault)
    monkeypatch.setattr(sources.retriever_hybrid, "invalidate_cache", lambda: None)
    monkeypatch.setattr(sources.catalog, "invalidate_catalog", lambda: None)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/sources/sync", json={"kind": "url_list"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "url_list"
    assert body["items_new"] == 1
    assert called == {"url_list": 1, "vault": 0}


async def test_post_sync_vault_dispatches_to_vault(monkeypatch):
    async def fake_sync_vault():
        return _summary("vault")

    monkeypatch.setattr(sources, "sync_vault", fake_sync_vault)
    monkeypatch.setattr(
        sources,
        "sync_url_list",
        lambda: pytest.fail("url_list should not have been called"),
    )
    monkeypatch.setattr(sources.retriever_hybrid, "invalidate_cache", lambda: None)
    monkeypatch.setattr(sources.catalog, "invalidate_catalog", lambda: None)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/sources/sync", json={"kind": "vault"})

    assert resp.status_code == 200
    assert resp.json()["kind"] == "vault"


async def test_post_sync_invalid_kind_returns_422(monkeypatch):
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/sources/sync", json={"kind": "nope"})

    # Pydantic Literal validation rejects unknown kinds with HTTP 422.
    assert resp.status_code == 422


async def test_post_sync_file_not_found_maps_to_400(monkeypatch):
    async def fake_sync_url_list():
        raise FileNotFoundError("URL list not found: /tmp/nope.md")

    monkeypatch.setattr(sources, "sync_url_list", fake_sync_url_list)
    monkeypatch.setattr(sources.retriever_hybrid, "invalidate_cache", lambda: None)
    monkeypatch.setattr(sources.catalog, "invalidate_catalog", lambda: None)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/sources/sync", json={"kind": "url_list"})

    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]


async def test_post_sync_value_error_maps_to_400(monkeypatch):
    async def fake_sync_vault():
        raise ValueError("SOURCE_VAULT_PATH is not configured.")

    monkeypatch.setattr(sources, "sync_vault", fake_sync_vault)
    monkeypatch.setattr(sources.retriever_hybrid, "invalidate_cache", lambda: None)
    monkeypatch.setattr(sources.catalog, "invalidate_catalog", lambda: None)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/sources/sync", json={"kind": "vault"})

    assert resp.status_code == 400
    assert "not configured" in resp.json()["detail"]


async def test_post_sync_invalidates_caches_even_on_failure(monkeypatch):
    invalidated = {"retriever": 0, "catalog": 0}

    async def fake_sync_url_list():
        raise FileNotFoundError("boom")

    def inv_retriever():
        invalidated["retriever"] += 1

    def inv_catalog():
        invalidated["catalog"] += 1

    monkeypatch.setattr(sources, "sync_url_list", fake_sync_url_list)
    monkeypatch.setattr(sources.retriever_hybrid, "invalidate_cache", inv_retriever)
    monkeypatch.setattr(sources.catalog, "invalidate_catalog", inv_catalog)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/sources/sync", json={"kind": "url_list"})

    assert invalidated == {"retriever": 1, "catalog": 1}


# ---------------------------------------------------------------------------
# GET /api/sources/sync-runs
# ---------------------------------------------------------------------------


async def test_get_sync_runs_returns_recent_runs(monkeypatch):
    async def fake_list_sync_runs(limit):
        assert limit == 10
        return [
            {
                "id": "run-1",
                "kind": "url_list",
                "status": "completed",
                "items_total": 2,
                "items_new": 2,
                "items_updated": 0,
                "items_unchanged": 0,
                "items_error": 0,
                "started_at": datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
                "finished_at": datetime(2026, 5, 1, 12, 1, tzinfo=UTC),
            }
        ]

    from backend.db import repository

    monkeypatch.setattr(repository, "list_sync_runs", fake_list_sync_runs)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources/sync-runs")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["sync_runs"]) == 1
    assert body["sync_runs"][0]["id"] == "run-1"
    assert body["sync_runs"][0]["kind"] == "url_list"


async def test_get_sync_runs_empty(monkeypatch):
    from backend.db import repository

    async def fake_list_sync_runs(limit):
        return []

    monkeypatch.setattr(repository, "list_sync_runs", fake_list_sync_runs)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources/sync-runs")

    assert resp.status_code == 200
    assert resp.json() == {"sync_runs": []}


# ---------------------------------------------------------------------------
# GET /api/sources/documents
# ---------------------------------------------------------------------------


async def test_get_documents_returns_admin_rows(monkeypatch):
    from backend.db import repository

    async def fake_list_documents_admin():
        return [
            {
                "id": "doc-1",
                "title": "FirstSpirit Module Manual",
                "description": "How to write modules",
                "url": "https://docs.example/m",
                "content_path": None,
                "source_type": "firstspirit",
                "lang": "en",
                "last_crawled_at": datetime(2026, 5, 1, tzinfo=UTC),
                "created_at": datetime(2026, 5, 1, tzinfo=UTC),
                "chunk_count": 42,
            }
        ]

    monkeypatch.setattr(repository, "list_documents_admin", fake_list_documents_admin)

    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources/documents")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["title"] == "FirstSpirit Module Manual"
    assert body["documents"][0]["chunk_count"] == 42

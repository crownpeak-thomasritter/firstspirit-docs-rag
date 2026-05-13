"""
Sources routes — trigger ingestion and inspect history.

Replaces the YouTube-era ``channels.py``. There are two ingest sources:

* ``url_list`` — crawl URLs from a markdown file. Configured via
  :data:`backend.config.SOURCE_URL_LIST_PATH`.
* ``vault`` — read markdown files from an Obsidian-style directory.
  Configured via :data:`backend.config.SOURCE_VAULT_PATH`.

Endpoints:

* ``POST /api/sources/sync`` — kick off one of the two pipelines.
* ``GET  /api/sources/sync-runs`` — recent ingest-run history.
* ``GET  /api/sources/documents`` — the document catalog (admin view, with
  chunk counts).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import repository as repo
from backend.ingest import sync_url_list, sync_vault
from backend.rag import catalog, retriever_hybrid

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


SyncKind = Literal["url_list", "vault"]


class SyncRequest(BaseModel):
    kind: SyncKind


class SyncResponse(BaseModel):
    sync_run_id: str
    kind: SyncKind
    status: str
    items_total: int
    items_new: int
    items_updated: int
    items_unchanged: int
    items_error: int


class SyncRun(BaseModel):
    id: str
    kind: SyncKind
    status: str
    items_total: int
    items_new: int
    items_updated: int
    items_unchanged: int
    items_error: int
    started_at: datetime
    finished_at: datetime | None


class SyncRunsResponse(BaseModel):
    sync_runs: list[SyncRun]


class DocumentRow(BaseModel):
    id: str
    title: str
    description: str
    url: str | None
    content_path: str | None
    source_type: str
    lang: str | None
    last_crawled_at: datetime | None
    created_at: datetime
    chunk_count: int


class DocumentsResponse(BaseModel):
    documents: list[DocumentRow]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sources/sync", response_model=SyncResponse)
async def trigger_sync(body: SyncRequest) -> SyncResponse:
    """Run one of the ingest pipelines synchronously.

    The call blocks until every URL or file has been processed — pace your
    timeouts accordingly. Failures on individual items don't fail the whole
    run; they're recorded on the ``source_sync_items`` row.
    """
    try:
        if body.kind == "url_list":
            summary = await sync_url_list()
        else:
            summary = await sync_vault()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        # Caches must drop their stale view even if the run raised midway.
        retriever_hybrid.invalidate_cache()
        catalog.invalidate_catalog()

    return SyncResponse(**summary)


@router.get("/sources/sync-runs", response_model=SyncRunsResponse)
async def list_sync_runs() -> SyncRunsResponse:
    """Return the 10 most recent ingest runs across both pipelines."""
    rows = await repo.list_sync_runs(limit=10)
    return SyncRunsResponse(sync_runs=[SyncRun(**row) for row in rows])


@router.get("/sources/documents", response_model=DocumentsResponse)
async def list_documents() -> DocumentsResponse:
    """Document catalog with per-document chunk counts (admin view)."""
    rows = await repo.list_documents_admin()
    return DocumentsResponse(documents=[DocumentRow(**row) for row in rows])

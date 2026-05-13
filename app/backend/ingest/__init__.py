"""Ingestion pipelines for the FirstSpirit docs RAG.

Two source kinds:

* :mod:`backend.ingest.url_list` — crawls a list of URLs from a markdown file.
* :mod:`backend.ingest.vault` — walks a directory of Obsidian markdown notes.

Both pipelines write to the ``documents`` and ``document_chunks`` tables and
emit a row per attempt to ``source_sync_runs`` / ``source_sync_items`` so the
admin UI can show ingest history.
"""

from __future__ import annotations

from backend.ingest.url_list import sync_url_list
from backend.ingest.vault import sync_vault

__all__ = ["sync_url_list", "sync_vault"]

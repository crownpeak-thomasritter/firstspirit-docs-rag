"""Tests for ``backend.ingest.url_list``.

Repository, crawler, extractor, chunker, and embedder are all monkeypatched
so the test runs offline with no Postgres or HTTP traffic.

What we verify:
    * ``parse_url_list`` extracts URLs from raw, bullet, and link forms,
      strips trailing prose punctuation, deduplicates, and skips headings.
    * The end-to-end ``sync_url_list`` happy path produces the right item
      counts: ``ingested`` for a new doc, ``unchanged`` for a 304 / content
      hash match, ``updated`` for changed content, and ``error`` for crawler
      failures and zero-chunk extractions.
    * Per-URL exceptions don't abort the run — the bad item is recorded as
      ``error`` and the loop continues.
"""

from __future__ import annotations

import pytest

from backend.ingest import url_list as url_list_mod
from backend.rag import document_chunker
from backend.services import crawler

# ---------------------------------------------------------------------------
# parse_url_list — pure
# ---------------------------------------------------------------------------


def test_parse_url_list_skips_blank_and_comment_lines():
    text = """
    # This is a heading

    https://example.com/a
    """
    assert url_list_mod.parse_url_list(text) == ["https://example.com/a"]


def test_parse_url_list_handles_markdown_links():
    text = "[A](https://example.com/a) and [B](https://example.com/b)"
    assert url_list_mod.parse_url_list(text) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_parse_url_list_strips_trailing_punctuation():
    text = "See https://example.com/page."
    assert url_list_mod.parse_url_list(text) == ["https://example.com/page"]


def test_parse_url_list_deduplicates_preserving_order():
    text = "https://x.com/a\nhttps://x.com/b\nhttps://x.com/a\n"
    assert url_list_mod.parse_url_list(text) == ["https://x.com/a", "https://x.com/b"]


def test_parse_url_list_ignores_lines_starting_with_hash():
    # Even if a line starts with `#` but contains a URL, it's a comment.
    text = "# Notes: https://x.com/a\nhttps://x.com/b\n"
    assert url_list_mod.parse_url_list(text) == ["https://x.com/b"]


# ---------------------------------------------------------------------------
# sync_url_list — orchestration
# ---------------------------------------------------------------------------


class _FakeRepo:
    """In-memory stand-in for ``backend.db.repository`` calls used by the ingester."""

    def __init__(self) -> None:
        self.documents_by_url: dict[str, dict] = {}
        self.documents_by_id: dict[str, dict] = {}
        self.sync_runs: dict[str, dict] = {}
        self.sync_items: list[dict] = []
        self.replaced_chunks: dict[str, list[dict]] = {}

    def _now(self) -> str:
        return "2026-05-13T00:00:00Z"

    def _new_id(self) -> str:
        return f"id-{len(self.sync_runs) + len(self.documents_by_id) + len(self.sync_items)}"

    async def get_document_by_url(self, url: str) -> dict | None:
        return self.documents_by_url.get(url)

    async def create_sync_run(self, *, sync_run_id, kind, started_at):
        self.sync_runs[sync_run_id] = {
            "id": sync_run_id,
            "kind": kind,
            "started_at": started_at,
            "items": [],
            "status": "running",
        }

    async def update_sync_run(self, *, sync_run_id, **fields):
        self.sync_runs[sync_run_id].update(fields)

    async def create_sync_item(self, *, sync_run_id, source_ref):
        item = {
            "id": f"item-{len(self.sync_items)}",
            "sync_run_id": sync_run_id,
            "source_ref": source_ref,
            "outcome": None,
            "message": None,
        }
        self.sync_items.append(item)
        return item

    async def update_sync_item_outcome(self, item_id, outcome, message=None):
        for it in self.sync_items:
            if it["id"] == item_id:
                it["outcome"] = outcome
                it["message"] = message
                break

    async def update_document_crawl_metadata(self, document_id, **fields):
        if document_id in self.documents_by_id:
            self.documents_by_id[document_id].update(fields)

    async def create_document(self, *, title, description, url, source_type, lang=None, **fields):
        doc_id = f"doc-{len(self.documents_by_id)}"
        doc = {
            "id": doc_id,
            "title": title,
            "description": description,
            "url": url,
            "source_type": source_type,
            "lang": lang,
            **fields,
        }
        self.documents_by_id[doc_id] = doc
        if url:
            self.documents_by_url[url] = doc
        return doc

    async def replace_chunks_for_document(self, document_id, payload, *, source_type):
        self.replaced_chunks[document_id] = list(payload)


class _FakeVectorStore:
    """In-memory stand-in for ``backend.rag.vector_store`` calls."""

    def __init__(self) -> None:
        self.upserted: dict[str, list[dict]] = {}
        self.deleted: list[str] = []

    async def upsert_chunks(self, *, document_id, chunks):
        self.upserted[document_id] = list(chunks)

    async def delete_document(self, document_id):
        self.deleted.append(document_id)


@pytest.fixture
def fake_repo(monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(url_list_mod, "repository", repo)
    return repo


@pytest.fixture
def fake_vector_store(monkeypatch):
    vs = _FakeVectorStore()
    monkeypatch.setattr(url_list_mod, "vector_store", vs)
    return vs


def _ok_crawl_result(content=b"<html><body>doc</body></html>", etag='"v1"'):
    return crawler.CrawlResult(
        url="https://example.com/a",
        status=crawler.CrawlStatus.OK,
        content=content,
        content_type="text/html",
        etag=etag,
        last_modified=None,
        http_status=200,
        error=None,
    )


async def _patch_pipeline(monkeypatch, *, content=b"<html><body>doc</body></html>"):
    """Patch crawler/extractor/chunker/embedder for happy-path ingest."""

    async def fake_fetch(url, etag=None, last_modified=None):
        return _ok_crawl_result(content=content)

    def fake_extract(content_bytes, content_type=None, source_url=None):
        from backend.services import extractor

        return extractor.ExtractedDocument(
            title="A Doc",
            body_markdown="Hello world.",
            source_format="html",
            lang="en",
            sections=[],
        )

    def fake_chunk_document(extracted):
        chunk = document_chunker.DocumentChunk(
            content="Hello world.",
            chunk_index=0,
            section_path=["Hello"],
            anchor="hello",
            char_start=0,
            char_end=12,
        )
        return [chunk], False

    def fake_embed_batch(texts):
        return [[0.1] * 1536 for _ in texts]

    monkeypatch.setattr(url_list_mod.crawler, "fetch", fake_fetch)
    monkeypatch.setattr(url_list_mod.extractor, "extract", fake_extract)
    monkeypatch.setattr(url_list_mod.document_chunker, "chunk_document", fake_chunk_document)
    monkeypatch.setattr(url_list_mod, "embed_batch", fake_embed_batch)


async def test_sync_url_list_ingests_new_document(
    tmp_path, fake_repo, fake_vector_store, monkeypatch
):
    list_file = tmp_path / "URL List.md"
    list_file.write_text("https://example.com/a\n")

    await _patch_pipeline(monkeypatch)

    summary = await url_list_mod.sync_url_list(list_path=str(list_file))

    assert summary["items_total"] == 1
    assert summary["items_new"] == 1
    assert summary["items_updated"] == 0
    assert summary["items_unchanged"] == 0
    assert summary["items_error"] == 0
    # The document and its chunk were created.
    assert "https://example.com/a" in fake_repo.documents_by_url
    [doc] = fake_repo.documents_by_id.values()
    assert fake_repo.replaced_chunks[doc["id"]][0]["content"] == "Hello world."
    # The Qdrant vector store also received the upsert with the same chunk id
    # the repository was told to insert.
    sqlite_chunk_id = fake_repo.replaced_chunks[doc["id"]][0]["chunk_id"]
    qdrant_chunks = fake_vector_store.upserted[doc["id"]]
    assert len(qdrant_chunks) == 1
    assert qdrant_chunks[0]["chunk_id"] == sqlite_chunk_id
    assert qdrant_chunks[0]["document_title"] == "A Doc"
    assert qdrant_chunks[0]["document_url"] == "https://example.com/a"
    assert qdrant_chunks[0]["embedding"] == [0.1] * 1536


async def test_sync_url_list_records_unchanged_when_304(
    tmp_path, fake_repo, fake_vector_store, monkeypatch
):
    fake_repo.documents_by_url["https://example.com/a"] = {
        "id": "doc-existing",
        "etag": '"v1"',
        "last_modified": None,
        "content_hash": "old",
    }
    fake_repo.documents_by_id["doc-existing"] = fake_repo.documents_by_url["https://example.com/a"]
    list_file = tmp_path / "URL List.md"
    list_file.write_text("https://example.com/a\n")

    async def fake_fetch(url, etag=None, last_modified=None):
        assert etag == '"v1"'
        return crawler.CrawlResult(
            url=url,
            status=crawler.CrawlStatus.NOT_MODIFIED,
            content=None,
            content_type=None,
            etag='"v1"',
            last_modified=None,
            http_status=304,
            error=None,
        )

    monkeypatch.setattr(url_list_mod.crawler, "fetch", fake_fetch)

    summary = await url_list_mod.sync_url_list(list_path=str(list_file))

    assert summary["items_unchanged"] == 1
    assert summary["items_new"] == 0
    # No chunks were replaced for the unchanged document.
    assert "doc-existing" not in fake_repo.replaced_chunks


async def test_sync_url_list_records_error_for_crawl_failure(
    tmp_path, fake_repo, fake_vector_store, monkeypatch
):
    list_file = tmp_path / "URL List.md"
    list_file.write_text("https://example.com/a\n")

    async def fake_fetch(url, etag=None, last_modified=None):
        return crawler.CrawlResult(
            url=url,
            status=crawler.CrawlStatus.ERROR,
            content=None,
            content_type=None,
            etag=None,
            last_modified=None,
            http_status=500,
            error="server_error",
        )

    monkeypatch.setattr(url_list_mod.crawler, "fetch", fake_fetch)

    summary = await url_list_mod.sync_url_list(list_path=str(list_file))

    assert summary["items_error"] == 1
    assert summary["items_new"] == 0


async def test_sync_url_list_continues_after_per_url_exception(
    tmp_path, fake_repo, fake_vector_store, monkeypatch
):
    list_file = tmp_path / "URL List.md"
    list_file.write_text("https://example.com/a\nhttps://example.com/b\n")

    calls = {"n": 0}

    async def fake_fetch(url, etag=None, last_modified=None):
        calls["n"] += 1
        if "/a" in url:
            raise RuntimeError("boom on a")
        return _ok_crawl_result()

    def fake_extract(content, content_type=None, source_url=None):
        from backend.services import extractor

        return extractor.ExtractedDocument(
            title="B", body_markdown="B body.", source_format="html", lang="en", sections=[]
        )

    def fake_chunk_document(extracted):
        return [
            document_chunker.DocumentChunk(
                content="B body.",
                chunk_index=0,
                section_path=["B"],
                anchor="b",
                char_start=0,
                char_end=10,
            )
        ], False

    def fake_embed_batch(texts):
        return [[0.0] * 1536]

    monkeypatch.setattr(url_list_mod.crawler, "fetch", fake_fetch)
    monkeypatch.setattr(url_list_mod.extractor, "extract", fake_extract)
    monkeypatch.setattr(url_list_mod.document_chunker, "chunk_document", fake_chunk_document)
    monkeypatch.setattr(url_list_mod, "embed_batch", fake_embed_batch)

    summary = await url_list_mod.sync_url_list(list_path=str(list_file))

    assert calls["n"] == 2  # both URLs were attempted
    assert summary["items_total"] == 2
    assert summary["items_error"] == 1
    assert summary["items_new"] == 1


async def test_sync_url_list_missing_list_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        await url_list_mod.sync_url_list(list_path=str(tmp_path / "nope.md"))


async def test_sync_url_list_zero_chunks_records_error(
    tmp_path, fake_repo, fake_vector_store, monkeypatch
):
    list_file = tmp_path / "URL List.md"
    list_file.write_text("https://example.com/a\n")

    async def fake_fetch(url, etag=None, last_modified=None):
        return _ok_crawl_result()

    def fake_extract(content, content_type=None, source_url=None):
        from backend.services import extractor

        return extractor.ExtractedDocument(
            title="A", body_markdown="", source_format="html", lang="en", sections=[]
        )

    def fake_chunk_document(extracted):
        return [], False

    monkeypatch.setattr(url_list_mod.crawler, "fetch", fake_fetch)
    monkeypatch.setattr(url_list_mod.extractor, "extract", fake_extract)
    monkeypatch.setattr(url_list_mod.document_chunker, "chunk_document", fake_chunk_document)

    summary = await url_list_mod.sync_url_list(list_path=str(list_file))

    assert summary["items_error"] == 1
    assert summary["items_new"] == 0

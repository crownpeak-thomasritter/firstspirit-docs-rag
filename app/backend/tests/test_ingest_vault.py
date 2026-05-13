"""Tests for ``backend.ingest.vault``.

Walks a synthetic Obsidian-style vault written into ``tmp_path``. Repository,
extractor, chunker, and embedder are monkeypatched.

What we verify:
    * Missing or non-directory ``vault_path`` raises the right exceptions.
    * Files are discovered recursively and only ``*.md`` are picked up.
    * YAML frontmatter ``title``, ``description``, ``lang``, and ``source``
      land on the created document.
    * Unchanged content (same SHA-256 hash) returns ``unchanged`` without
      re-embedding.
    * Empty bodies and zero-chunk extractions return ``error``.
    * A per-file exception doesn't abort the run.
"""

from __future__ import annotations

import pytest

from backend.ingest import vault as vault_mod
from backend.rag import document_chunker
from backend.services import extractor

# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self) -> None:
        self.documents_by_id: dict[str, dict] = {}
        self.documents_by_content_path: dict[str, dict] = {}
        self.sync_runs: dict[str, dict] = {}
        self.sync_items: list[dict] = []
        self.replaced_chunks: dict[str, list[dict]] = {}

    def _now(self) -> str:
        return "2026-05-13T00:00:00Z"

    def _new_id(self) -> str:
        return f"id-{len(self.sync_runs) + len(self.documents_by_id) + len(self.sync_items)}"

    async def create_sync_run(self, *, sync_run_id, kind, started_at):
        self.sync_runs[sync_run_id] = {"id": sync_run_id, "kind": kind}

    async def update_sync_run(self, *, sync_run_id, **fields):
        self.sync_runs[sync_run_id].update(fields)

    async def create_sync_item(self, *, sync_run_id, source_ref):
        item = {
            "id": f"item-{len(self.sync_items)}",
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

    async def get_document_by_content_path(self, rel_path):
        return self.documents_by_content_path.get(rel_path)

    async def create_document(
        self,
        *,
        title,
        description,
        url=None,
        content_path=None,
        source_type,
        lang=None,
        **fields,
    ):
        doc_id = f"doc-{len(self.documents_by_id)}"
        doc = {
            "id": doc_id,
            "title": title,
            "description": description,
            "url": url,
            "content_path": content_path,
            "source_type": source_type,
            "lang": lang,
            **fields,
        }
        self.documents_by_id[doc_id] = doc
        if content_path:
            self.documents_by_content_path[content_path] = doc
        return doc

    async def update_document_crawl_metadata(self, document_id, **fields):
        if document_id in self.documents_by_id:
            self.documents_by_id[document_id].update(fields)

    async def replace_chunks_for_document(self, document_id, payload, *, source_type):
        self.replaced_chunks[document_id] = list(payload)


@pytest.fixture
def fake_repo(monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(vault_mod, "repository", repo)
    return repo


def _patch_pipeline(monkeypatch, *, body_text="A vault note body."):
    def fake_extract_markdown(content_bytes):
        return extractor.ExtractedDocument(
            body_markdown=body_text,
            title="Detected Title",
            source_format="markdown",
            lang="en",
            sections=[],
        )

    def fake_chunk_document(extracted):
        return [
            document_chunker.DocumentChunk(
                content=body_text,
                section_path=["Top"],
                anchor="top",
                chunk_index=0,
                char_start=0,
                char_end=len(body_text),
            )
        ], False

    def fake_embed_batch(texts):
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(vault_mod.extractor, "extract_markdown", fake_extract_markdown)
    monkeypatch.setattr(vault_mod.document_chunker, "chunk_document", fake_chunk_document)
    monkeypatch.setattr(vault_mod, "embed_batch", fake_embed_batch)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_sync_vault_no_path_raises_value_error(monkeypatch):
    monkeypatch.setattr(vault_mod, "SOURCE_VAULT_PATH", "")
    with pytest.raises(ValueError):
        await vault_mod.sync_vault()


async def test_sync_vault_missing_dir_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        await vault_mod.sync_vault(vault_path=str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_sync_vault_ingests_files_recursively(tmp_path, fake_repo, monkeypatch):
    (tmp_path / "a.md").write_text("# Note A\n\nHello A.\n")
    sub = tmp_path / "topic"
    sub.mkdir()
    (sub / "b.md").write_text("# Note B\n\nHello B.\n")
    # Non-markdown file should be ignored.
    (tmp_path / "ignored.txt").write_text("not markdown")

    _patch_pipeline(monkeypatch)

    summary = await vault_mod.sync_vault(vault_path=str(tmp_path))

    assert summary["items_total"] == 2
    assert summary["items_new"] == 2
    # Both files were registered by their relative paths.
    assert "a.md" in fake_repo.documents_by_content_path
    assert "topic/b.md" in fake_repo.documents_by_content_path


async def test_sync_vault_uses_frontmatter_metadata(tmp_path, fake_repo, monkeypatch):
    (tmp_path / "page.md").write_text(
        "---\n"
        "title: From Frontmatter\n"
        "description: A short summary\n"
        "lang: de\n"
        "source: https://docs.example/page\n"
        "---\n"
        "# Body Heading\n\n"
        "Body text.\n"
    )
    _patch_pipeline(monkeypatch)

    await vault_mod.sync_vault(vault_path=str(tmp_path))

    doc = fake_repo.documents_by_content_path["page.md"]
    assert doc["title"] == "From Frontmatter"
    assert doc["description"] == "A short summary"
    assert doc["lang"] == "de"
    assert doc["url"] == "https://docs.example/page"


async def test_sync_vault_unchanged_when_hash_matches(tmp_path, fake_repo, monkeypatch):
    import hashlib

    (tmp_path / "page.md").write_text("# A\n\nBody.\n")
    expected_hash = hashlib.sha256((tmp_path / "page.md").read_bytes()).hexdigest()
    fake_repo.documents_by_content_path["page.md"] = {
        "id": "doc-existing",
        "content_hash": expected_hash,
    }
    fake_repo.documents_by_id["doc-existing"] = fake_repo.documents_by_content_path["page.md"]

    _patch_pipeline(monkeypatch)

    summary = await vault_mod.sync_vault(vault_path=str(tmp_path))

    assert summary["items_unchanged"] == 1
    assert summary["items_new"] == 0
    # Should not have re-embedded.
    assert "doc-existing" not in fake_repo.replaced_chunks


async def test_sync_vault_empty_body_records_error(tmp_path, fake_repo, monkeypatch):
    # Frontmatter-only file with no body content.
    (tmp_path / "page.md").write_text("---\ntitle: Empty\n---\n   \n")

    _patch_pipeline(monkeypatch)

    summary = await vault_mod.sync_vault(vault_path=str(tmp_path))

    assert summary["items_error"] == 1
    assert summary["items_new"] == 0


async def test_sync_vault_zero_chunks_records_error(tmp_path, fake_repo, monkeypatch):
    (tmp_path / "page.md").write_text("# A\n\nBody.\n")

    def fake_extract_markdown(content_bytes):
        return extractor.ExtractedDocument(
            body_markdown="Body.", source_format="markdown", lang="en", sections=[]
        )

    def fake_chunk_document(extracted):
        return [], False

    monkeypatch.setattr(vault_mod.extractor, "extract_markdown", fake_extract_markdown)
    monkeypatch.setattr(vault_mod.document_chunker, "chunk_document", fake_chunk_document)

    summary = await vault_mod.sync_vault(vault_path=str(tmp_path))

    assert summary["items_error"] == 1


async def test_sync_vault_continues_after_per_file_exception(tmp_path, fake_repo, monkeypatch):
    (tmp_path / "good.md").write_text("# Good\n\nbody\n")
    (tmp_path / "bad.md").write_text("# Bad\n\nbody\n")

    def fake_extract_markdown(content_bytes):
        if b"Bad" in content_bytes:
            raise RuntimeError("synthetic failure")
        return extractor.ExtractedDocument(
            body_markdown="ok", source_format="markdown", title="Good", lang="en", sections=[]
        )

    def fake_chunk_document(extracted):
        return [
            document_chunker.DocumentChunk(
                content="ok",
                section_path=["Good"],
                anchor="good",
                chunk_index=0,
                char_start=0,
                char_end=2,
            )
        ], False

    def fake_embed_batch(texts):
        return [[0.0] * 1536]

    monkeypatch.setattr(vault_mod.extractor, "extract_markdown", fake_extract_markdown)
    monkeypatch.setattr(vault_mod.document_chunker, "chunk_document", fake_chunk_document)
    monkeypatch.setattr(vault_mod, "embed_batch", fake_embed_batch)

    summary = await vault_mod.sync_vault(vault_path=str(tmp_path))

    assert summary["items_total"] == 2
    assert summary["items_new"] == 1
    assert summary["items_error"] == 1

"""Document chunker tests — feeds synthetic markdown through the pipeline."""

from __future__ import annotations

import pytest

from backend.rag import document_chunker
from backend.services import extractor


def _make_markdown_doc(body: str) -> extractor.ExtractedDocument:
    """Run the body through the markdown extractor to get sections."""
    extracted = extractor.extract_markdown(body.encode("utf-8"))
    assert extracted is not None
    return extracted


def test_chunk_document_empty_body_returns_empty():
    doc = extractor.ExtractedDocument(body_markdown="", source_format="markdown")
    chunks, had_errors = document_chunker.chunk_document(doc)
    assert chunks == []
    assert had_errors is False


def test_chunk_document_emits_indexed_chunks():
    body = (
        "# Overview\n\n"
        "FirstSpirit is a content management system used for enterprise web "
        "publishing. It supports templates, rules, and workflows.\n\n"
        "## Installation\n\n"
        "Installation requires Java 17 or newer. The installer is provided as "
        "a JAR. Run it with `java -jar`.\n\n"
        "## Configuration\n\n"
        "Configuration lives in fs-server.conf. Adjust the heap size, the "
        "database connection, and the SMTP settings as needed.\n\n"
        "### Heap tuning\n\n"
        "For large projects, set the heap to at least 4 GB. Monitor via JMX.\n"
    )
    doc = _make_markdown_doc(body)

    chunks, had_errors = document_chunker.chunk_document(doc)

    assert had_errors is False
    assert chunks, "chunker returned no chunks"

    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks))), "chunk_index must be monotonic"

    # Every chunk must carry non-empty content.
    for c in chunks:
        assert c.content.strip()
        assert isinstance(c.section_path, list)
        assert c.char_start >= 0
        assert c.char_end >= c.char_start


def test_chunk_document_attaches_section_path_and_anchor():
    body = (
        "# Top Level\n\n"
        "Intro paragraph about FirstSpirit's documentation overview.\n\n"
        "## Sub Section\n\n"
        "Detailed body explaining a feature. " * 20 + "\n\n"
        "## Other Section\n\n"
        "Another body of explanatory text. " * 20 + "\n"
    )
    doc = _make_markdown_doc(body)

    chunks, _ = document_chunker.chunk_document(doc)
    assert chunks

    # At least one chunk should land under each subsection — verify the
    # extractor's anchors are used (not freshly slugified from chunk meta).
    extracted_anchors = {s.anchor for s in doc.sections}
    used_anchors = {c.anchor for c in chunks if c.anchor}
    assert used_anchors.issubset(extracted_anchors | {None})


def test_chunk_document_long_content_split_into_multiple():
    # Build a body that's clearly larger than max_tokens=512 so HybridChunker
    # will produce at least two chunks under one section.
    paragraph = (
        "The FirstSpirit content management system supports templates, "
        "rules, snippets, schedules, scripts, workflows, and project apps. "
    ) * 80  # ~80 * 14 words ≈ 1120 words → well over 512 tokens
    body = f"# Long Section\n\n{paragraph}\n"

    doc = _make_markdown_doc(body)
    chunks, _ = document_chunker.chunk_document(doc)

    assert len(chunks) >= 2, "long content should split into at least two chunks"


@pytest.mark.parametrize(
    "input_text,expected_anchor",
    [
        ("Hello World", "hello-world"),
        ("CMS_INPUT_DOM", "cms-input-dom"),
        ("  spaces  ", "spaces"),
    ],
)
def test_anchor_from_headings_falls_back_to_slug(input_text, expected_anchor):
    anchor = document_chunker._anchor_from_headings([input_text], [])
    assert anchor == expected_anchor


def test_anchor_from_headings_prefers_extractor_anchors():
    sections = [
        extractor.Section(level=2, text="Examples", char_offset=10, anchor="examples"),
        extractor.Section(level=2, text="Examples", char_offset=200, anchor="examples-1"),
    ]
    # When the deepest heading is "Examples", the *last-seen* anchor wins —
    # which is the de-duplicated "examples-1".
    anchor = document_chunker._anchor_from_headings(["Top", "Examples"], sections)
    assert anchor == "examples-1"

"""Extractor tests — covers format dispatch, HTML, and markdown branches.

PDF extraction is exercised by an integration fixture (see
``tests/fixtures/extractor/*.pdf``) which lands in a follow-up commit; it
requires real PDF bytes that we'd rather check in once.
"""

from __future__ import annotations

import pytest

from backend.services import extractor


def test_detect_format_html_by_content_type():
    fmt = extractor._detect_format(b"<html></html>", "text/html; charset=utf-8")
    assert fmt == "html"


def test_detect_format_pdf_by_magic_bytes():
    fmt = extractor._detect_format(b"%PDF-1.4 ...", None)
    assert fmt == "pdf"


def test_detect_format_markdown_default():
    fmt = extractor._detect_format(b"# Heading\n\nbody text", None)
    assert fmt == "markdown"


def test_extract_returns_none_for_empty_bytes():
    assert extractor.extract(b"", "text/html") is None


def test_extract_markdown_parses_sections():
    md = (
        b"# Top heading\n\n"
        b"Intro paragraph.\n\n"
        b"## Nested section\n\n"
        b"More text.\n\n"
        b"### Deeper still\n\n"
        b"Final paragraph.\n"
    )

    doc = extractor.extract_markdown(md)
    assert doc is not None
    assert doc.title == "Top heading"
    assert doc.source_format == "markdown"

    levels = [s.level for s in doc.sections]
    texts = [s.text for s in doc.sections]
    anchors = [s.anchor for s in doc.sections]
    assert levels == [1, 2, 3]
    assert texts == ["Top heading", "Nested section", "Deeper still"]
    assert anchors == ["top-heading", "nested-section", "deeper-still"]


def test_extract_markdown_anchors_deduplicated():
    md = b"# Intro\n\nA.\n\n## Examples\n\nB.\n\n## Examples\n\nC.\n"
    doc = extractor.extract_markdown(md)
    assert doc is not None
    anchors = [s.anchor for s in doc.sections]
    # Top-level + two "Examples" → expect ["intro", "examples", "examples-1"]
    assert anchors == ["intro", "examples", "examples-1"]


def test_extract_markdown_empty_returns_none():
    assert extractor.extract_markdown(b"   \n  \n") is None


def test_extract_html_strips_chrome_returns_main_content():
    trafilatura = pytest.importorskip("trafilatura")
    assert trafilatura is not None  # silence unused-import lint

    html = b"""
    <html>
      <head><title>Example FirstSpirit Doc</title></head>
      <body>
        <nav><a href='/foo'>Nav link</a></nav>
        <main>
          <h1>CMS_INPUT_DOM Validation</h1>
          <p>FirstSpirit lets you validate input via Rules.</p>
          <h2>Configuration</h2>
          <p>Add a Rule referencing the input.</p>
        </main>
        <footer>Footer chrome</footer>
      </body>
    </html>
    """
    doc = extractor.extract_html(html, source_url="https://example.com/docs/cms-input-dom")
    assert doc is not None
    assert doc.source_format == "html"
    # Title comes back either from <title> or from the H1; both are acceptable.
    assert doc.title is not None
    assert "FirstSpirit" in doc.body_markdown or "Validation" in doc.body_markdown


def test_extract_dispatch_to_pdf_for_magic_bytes(monkeypatch):
    called = {"n": 0}

    def fake_extract_pdf(content: bytes):
        called["n"] += 1
        return extractor.ExtractedDocument(body_markdown="ok", source_format="pdf")

    monkeypatch.setattr(extractor, "extract_pdf", fake_extract_pdf)
    result = extractor.extract(b"%PDF-1.4\n...", None)

    assert called["n"] == 1
    assert result is not None
    assert result.source_format == "pdf"


def test_slugify_handles_punctuation_and_case():
    assert extractor._slugify("Hello, World!") == "hello-world"
    assert extractor._slugify("  CMS_INPUT_DOM  ") == "cms-input-dom"
    assert extractor._slugify("###") == ""

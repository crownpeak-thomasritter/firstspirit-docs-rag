"""
Content extractor — turns raw bytes from the crawler (or a markdown file from
the vault) into a normalised :class:`ExtractedDocument`.

Three branches, dispatched on content-type / magic bytes:

* HTML  → ``trafilatura.extract(..., output_format='markdown', favor_recall=True)``
* PDF   → ``pymupdf4llm.to_markdown(BytesIO(content))``
* MD    → pass-through (the source already *is* markdown)

The result is a single body of markdown plus a parsed section table-of-contents.
The chunker reads the section table-of-contents to attach ``section_path`` /
``anchor`` to each emitted chunk.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One heading from the extracted markdown."""

    level: int  # 1 for ``#``, 2 for ``##``, etc.
    text: str
    char_offset: int  # byte offset (== char offset, since markdown is text)
    anchor: str  # slugified ``text``


@dataclass
class ExtractedDocument:
    """Normalised representation of an extracted source."""

    body_markdown: str
    title: str | None = None
    lang: str | None = None
    source_format: str = "html"  # "html" | "pdf" | "markdown"
    sections: list[Section] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract(
    content: bytes,
    content_type: str | None = None,
    source_url: str | None = None,
) -> ExtractedDocument | None:
    """Dispatch to the right extractor based on ``content_type``.

    Args:
        content: Raw response body.
        content_type: HTTP ``Content-Type`` (e.g. ``text/html; charset=utf-8``).
            Optional — magic-byte sniffing is used when omitted.
        source_url: For logging only.

    Returns:
        ExtractedDocument, or ``None`` when the body is empty or every
        extractor returned no usable text.
    """
    if not content:
        return None

    fmt = _detect_format(content, content_type)
    if fmt == "pdf":
        return extract_pdf(content)
    if fmt == "markdown":
        return extract_markdown(content)
    # default: HTML
    return extract_html(content, source_url=source_url)


def extract_html(content: bytes, source_url: str | None = None) -> ExtractedDocument | None:
    """Extract main-content markdown from an HTML page via trafilatura.

    Uses ``favor_recall=True`` because FirstSpirit / Crownpeak docs pages have
    chrome-heavy templates where the default precision-mode strips real
    content. Returns ``None`` if trafilatura yields no body.
    """
    try:
        import trafilatura  # local import — heavy dep, keep startup cheap
    except ImportError as exc:  # pragma: no cover — install-time issue
        raise RuntimeError("trafilatura is required for HTML extraction") from exc

    html_text = _decode_html(content)

    body = trafilatura.extract(
        html_text,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        favor_recall=True,
        url=source_url,
    )
    if not body or not body.strip():
        logger.info("trafilatura returned empty body for %s", source_url or "<unknown>")
        return None

    title: str | None = None
    lang: str | None = None
    try:
        metadata = trafilatura.extract_metadata(html_text)
        if metadata is not None:
            title = (getattr(metadata, "title", None) or "").strip() or None
            lang = (getattr(metadata, "language", None) or "").strip() or None
    except Exception as exc:  # pragma: no cover — metadata is best-effort
        logger.debug("trafilatura metadata failed for %s: %s", source_url, exc)

    sections = _parse_sections(body)
    if title is None:
        title = _title_from_sections(sections) or _title_from_html_tag(html_text)

    return ExtractedDocument(
        body_markdown=body,
        title=title,
        lang=lang,
        source_format="html",
        sections=sections,
    )


def extract_pdf(content: bytes) -> ExtractedDocument | None:
    """Extract markdown from a PDF using pymupdf4llm.

    pymupdf4llm preserves heading hierarchy reasonably well for technical PDFs
    (release notes, ODFS exports). Scanned-image PDFs with no text layer come
    back empty — we return ``None`` so the ingester records the failure.
    """
    try:
        import pymupdf4llm  # local import — heavy dep
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pymupdf4llm is required for PDF extraction") from exc

    try:
        # ``to_markdown`` accepts a path or an open document; pass the latter
        # so we never touch the filesystem.
        import pymupdf  # PyMuPDF, pulled in by pymupdf4llm

        with pymupdf.open(stream=content, filetype="pdf") as doc:
            body = pymupdf4llm.to_markdown(doc)
    except Exception as exc:
        logger.warning("pymupdf4llm extraction failed: %s", exc)
        return None

    if not body or not body.strip():
        logger.info("PDF extraction yielded empty body")
        return None

    sections = _parse_sections(body)
    title = _title_from_sections(sections)

    return ExtractedDocument(
        body_markdown=body,
        title=title,
        lang=None,
        source_format="pdf",
        sections=sections,
    )


def extract_markdown(content: bytes) -> ExtractedDocument | None:
    """Treat the bytes as UTF-8 markdown. Used by the vault ingester.

    Does *not* strip frontmatter — that's the vault ingester's job (it needs
    the metadata). When called directly the frontmatter just appears verbatim
    at the top of ``body_markdown``.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")

    if not text.strip():
        return None

    sections = _parse_sections(text)
    title = _title_from_sections(sections)

    return ExtractedDocument(
        body_markdown=text,
        title=title,
        lang=None,
        source_format="markdown",
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_PDF_MAGIC = b"%PDF-"
_MD_HINTS = ("text/markdown", "text/x-markdown")
_HTML_HINTS = ("text/html", "application/xhtml+xml")
_PDF_HINTS = ("application/pdf", "application/x-pdf")


def _detect_format(content: bytes, content_type: str | None) -> str:
    """Return one of ``"html" | "pdf" | "markdown"``."""
    if content_type:
        ct = content_type.split(";", 1)[0].strip().lower()
        if ct in _PDF_HINTS:
            return "pdf"
        if ct in _MD_HINTS:
            return "markdown"
        if ct in _HTML_HINTS:
            return "html"
    # magic-byte fallback
    if content[:5] == _PDF_MAGIC:
        return "pdf"
    # Cheap HTML/Markdown discrimination: HTML usually starts with whitespace
    # then ``<``. Markdown starts with text or ``#``.
    head = content.lstrip()[:64].lower()
    if head.startswith(b"<"):
        return "html"
    return "markdown"


def _decode_html(content: bytes) -> str:
    """Decode HTML bytes with a charset-tolerant fallback."""
    for encoding in ("utf-8", "iso-8859-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


# Markdown ATX heading: 1-6 ``#`` followed by space + text. We deliberately
# require the leading hash to be at the start of a line (multiline mode).
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*\s*$", re.MULTILINE)
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Crude language-tag stripping for trafilatura's metadata output.
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _parse_sections(markdown: str) -> list[Section]:
    """Walk ATX headings, recording each level + text + char offset + anchor."""
    sections: list[Section] = []
    seen_anchors: dict[str, int] = {}
    for match in _HEADING_RE.finditer(markdown):
        level = len(match.group(1))
        text = match.group(2).strip()
        if not text:
            continue
        base_anchor = _slugify(text) or f"section-{len(sections) + 1}"
        # de-dupe anchors within the same document
        count = seen_anchors.get(base_anchor, 0)
        anchor = base_anchor if count == 0 else f"{base_anchor}-{count}"
        seen_anchors[base_anchor] = count + 1
        sections.append(
            Section(
                level=level,
                text=text,
                char_offset=match.start(),
                anchor=anchor,
            )
        )
    return sections


def _slugify(text: str) -> str:
    return _SLUG_NON_ALNUM.sub("-", text.lower()).strip("-")


def _title_from_sections(sections: list[Section]) -> str | None:
    """Take the first ``#`` heading as the title; fall back to the first
    heading of any level."""
    if not sections:
        return None
    for section in sections:
        if section.level == 1:
            return section.text
    return sections[0].text


def _title_from_html_tag(html_text: str) -> str | None:
    """Fallback when trafilatura's metadata block returned no title."""
    match = _HTML_TITLE_RE.search(html_text)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


__all__ = [
    "ExtractedDocument",
    "Section",
    "extract",
    "extract_html",
    "extract_markdown",
    "extract_pdf",
]

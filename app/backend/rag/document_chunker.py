"""
Document chunker — adapts the Docling HybridChunker to FirstSpirit / Crownpeak
documentation pages.

Given an :class:`backend.services.extractor.ExtractedDocument`, produces a list
of :class:`DocumentChunk` records. Each chunk carries:

* ``content`` — the contextualised chunk text ready to be embedded.
* ``section_path`` — heading breadcrumb (from Docling's ``chunk.meta.headings``).
* ``anchor`` — slug of the deepest heading; matches the anchor produced by the
  extractor so a citation can link to ``url#anchor``.
* ``chunk_index`` — position in the document; used by the retrieval-time
  neighbour-expansion logic.
* ``char_start`` / ``char_end`` — best-effort byte offsets back into the
  source markdown, derived from a forward-scanning cursor.

Replaces the YouTube-specific ``chunker.chunk_video_*`` family — the timestamp
fields are gone; section anchors are the new "where in the source" handle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import tiktoken
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
from docling_core.types.doc.document import DocItemLabel, DoclingDocument

from backend.config import HYBRID_CHUNKER_MAX_TOKENS
from backend.services.extractor import ExtractedDocument, Section

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentChunk:
    """One output chunk, ready for embedding + storage."""

    content: str
    section_path: list[str]
    anchor: str | None
    chunk_index: int
    char_start: int
    char_end: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_document(extracted: ExtractedDocument) -> tuple[list[DocumentChunk], bool]:
    """Chunk an extracted document via Docling HybridChunker.

    Args:
        extracted: The output of ``services.extractor.extract(...)``.

    Returns:
        ``(chunks, had_errors)``. ``had_errors`` is ``True`` when any chunk
        fell back to the raw text (the contextualize call raised) — the chunk
        is still emitted, callers may log but should not abort.
    """
    body = extracted.body_markdown
    if not body or not body.strip():
        return [], False

    doc = _build_docling_document(extracted)
    chunker = _make_chunker()

    raw_chunks: list[_RawChunk] = []
    had_errors = False
    try:
        for chunk in chunker.chunk(doc):
            try:
                contextualized = chunker.contextualize(chunk) or ""
                content = contextualized.strip()
            except Exception as exc:
                logger.warning("contextualize failed for chunk: %s", exc)
                content = (getattr(chunk, "text", "") or "").strip()
                had_errors = True

            if not content:
                continue

            headings = _extract_headings(chunk)
            raw_chunks.append(_RawChunk(content=content, headings=headings))
    except Exception as exc:
        logger.warning("HybridChunker raised; falling back to single-chunk body: %s", exc)
        raw_chunks = [_RawChunk(content=body.strip(), headings=[])]
        had_errors = True

    if not raw_chunks:
        return [], had_errors

    # Attach char offsets via a forward-scanning cursor and assign chunk_index.
    out: list[DocumentChunk] = []
    cursor = 0
    body_len = len(body)
    for idx, rc in enumerate(raw_chunks):
        char_start, char_end = _locate(rc.content, body, cursor)
        if char_start >= 0:
            cursor = char_end
        else:
            # Best-effort fallback: stamp the chunk at the current cursor
            char_start = cursor
            char_end = min(cursor + len(rc.content), body_len)
            cursor = char_end

        anchor = _anchor_from_headings(rc.headings, extracted.sections)
        out.append(
            DocumentChunk(
                content=rc.content,
                section_path=list(rc.headings),
                anchor=anchor,
                chunk_index=idx,
                char_start=char_start,
                char_end=char_end,
            )
        )

    return out, had_errors


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass
class _RawChunk:
    content: str
    headings: list[str]


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _make_chunker() -> HybridChunker:
    tokenizer = OpenAITokenizer(
        tokenizer=tiktoken.get_encoding("cl100k_base"),
        max_tokens=HYBRID_CHUNKER_MAX_TOKENS,
    )
    return HybridChunker(tokenizer=tokenizer, merge_peers=True)


def _build_docling_document(extracted: ExtractedDocument) -> DoclingDocument:
    """Build a DoclingDocument that preserves the heading hierarchy.

    Strategy: split the body by lines, classify each non-empty line as
    ``SECTION_HEADER`` (when it matches a recorded :class:`Section`) or
    ``PARAGRAPH`` (otherwise). Adjacent paragraphs are merged into a single
    PARAGRAPH item so Docling's chunker has whole paragraphs to reason about.

    The extractor already gives us a precise heading table-of-contents, so we
    don't need to re-implement ATX-heading detection here.
    """
    name = extracted.title or "document"
    doc = DoclingDocument(name=name)

    if extracted.title:
        doc.add_text(label=DocItemLabel.TITLE, text=extracted.title)

    # Pre-compute the set of heading texts so the line walker can match in O(1).
    heading_texts = {s.text for s in extracted.sections}

    body = extracted.body_markdown
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buffer:
            text = "\n".join(paragraph_buffer).strip()
            if text:
                doc.add_text(label=DocItemLabel.PARAGRAPH, text=text)
            paragraph_buffer.clear()

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip("# ").strip()

        # ATX heading line
        if line.startswith("#") and stripped in heading_texts:
            flush_paragraph()
            doc.add_text(label=DocItemLabel.SECTION_HEADER, text=stripped)
            continue

        if not line.strip():
            flush_paragraph()
            continue

        paragraph_buffer.append(line)

    flush_paragraph()
    return doc


def _extract_headings(chunk: object) -> list[str]:
    """Pull ``chunk.meta.headings`` defensively (Docling may omit it)."""
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return []
    headings = getattr(meta, "headings", None)
    if not headings:
        return []
    return [str(h) for h in headings if h]


def _anchor_from_headings(headings: list[str], sections: list[Section]) -> str | None:
    """Map the chunk's deepest heading back to an anchor.

    Prefers an exact match against the extractor's parsed sections (those
    anchors are already de-duplicated). Falls back to slugifying the heading
    text on the spot.
    """
    if not headings:
        return None
    last = headings[-1]
    for section in reversed(sections):
        if section.text == last:
            return section.anchor
    slug = _SLUG_NON_ALNUM.sub("-", last.lower()).strip("-")
    return slug or None


def _locate(needle: str, haystack: str, start_hint: int) -> tuple[int, int]:
    """Best-effort forward search for ``needle`` inside ``haystack``.

    Tries (1) an exact substring search from ``start_hint``, (2) the first
    line of the needle as a probe, then (3) returns ``(-1, -1)`` so the
    caller can fall back to its cursor.
    """
    if not needle:
        return -1, -1

    idx = haystack.find(needle, start_hint)
    if idx >= 0:
        return idx, idx + len(needle)

    # Wrap once in case the chunker reordered slightly.
    idx = haystack.find(needle, 0)
    if idx >= 0:
        return idx, idx + len(needle)

    # Probe with the needle's first non-empty line — typical when Docling
    # contextualises with a heading breadcrumb prepended to the chunk.
    first_line = next((ln for ln in needle.splitlines() if ln.strip()), "")
    if first_line and len(first_line) >= 12:
        idx = haystack.find(first_line, start_hint)
        if idx >= 0:
            return idx, idx + len(needle)

    return -1, -1


__all__ = ["DocumentChunk", "chunk_document"]

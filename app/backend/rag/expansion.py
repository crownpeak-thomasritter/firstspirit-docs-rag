"""
Chunk expansion — fetch neighbouring chunks within the same document and
merge contiguous spans into a single contextual unit.

The retriever returns whichever individual chunks scored highest under RRF.
Two consecutive chunks under the same heading should be presented as one
span rather than two near-duplicate citations — that's what this module
does. The "anchor" chunk (the one originally retrieved) keeps its
``chunk_id`` and ``section_path`` / ``anchor`` for citation rendering.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from backend.db import repository

logger = logging.getLogger(__name__)


async def expand_and_merge(
    chunks: list[dict],
    window: int = 1,
    _fetch_neighbors: Callable[[str, int, int], Awaitable[list[dict]]] | None = None,
) -> list[dict]:
    """Expand retrieved chunks by neighbours and merge into contiguous spans.

    Args:
        chunks: Citation-shaped chunk dicts from
            :func:`backend.rag.retriever_hybrid.retrieve_hybrid`. Required
            keys per chunk: ``chunk_id``, ``document_id``, ``document_title``,
            ``document_url``, ``content``, ``chunk_index``, ``section_path``,
            ``anchor``, ``source_type``.
        window: Number of neighbours on each side to fetch (default 1).
            ``0`` returns the input list unchanged.
        _fetch_neighbors: Test-only override. Defaults to
            :func:`backend.db.repository.get_chunk_neighbors`.

    Returns:
        A list of span dicts. The shape mirrors the input plus a merged
        ``content`` string. The span's ``section_path`` / ``anchor`` /
        ``chunk_id`` come from the originally retrieved chunk in the span so
        a citation still deep-links to the heading the user's query
        actually hit.
    """
    if window <= 0 or not chunks:
        return chunks

    if _fetch_neighbors is None:
        _fetch_neighbors = repository.get_chunk_neighbors

    # Index of (document_id, chunk_index) -> originally retrieved chunk. Keyed
    # by tuple so chunks at the same chunk_index across different documents
    # don't shadow each other.
    retrieved_by_index: dict[tuple[str, int], dict] = {}
    for c in chunks:
        retrieved_by_index[(c["document_id"], c["chunk_index"])] = c

    # Group originals by document and fetch neighbours per document.
    document_groups: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        document_groups[chunk["document_id"]].append(chunk)

    all_chunks: list[dict] = list(chunks)
    for document_id, doc_chunks in document_groups.items():
        logger.debug("Expanding %d chunks for document %s", len(doc_chunks), document_id)
        neighbor_tasks = [
            _fetch_neighbors(document_id, c["chunk_index"], window) for c in doc_chunks
        ]
        task_results = await asyncio.gather(*neighbor_tasks, return_exceptions=True)
        for task_result in task_results:
            if isinstance(task_result, BaseException):
                logger.warning(
                    "Neighbor fetch failed for document %s: %s", document_id, task_result
                )
                continue
            for n in task_result:
                neighbour = dict(n)
                neighbour["document_id"] = document_id
                all_chunks.append(neighbour)

    # Re-group everything (originals + neighbours) by document for merge.
    by_document: dict[str, list[dict]] = defaultdict(list)
    for c in all_chunks:
        by_document[c["document_id"]].append(c)

    merged: list[dict] = []
    for current_document_id, doc_chunks in by_document.items():
        seen: set[str] = set()
        unique_chunks: list[dict] = []
        for c in doc_chunks:
            cid = c.get("chunk_id") or c.get("id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            unique_chunks.append(c)

        unique_chunks.sort(key=lambda x: x["chunk_index"])

        # Group consecutive indices into raw spans.
        raw_spans: list[list[dict]] = []
        for chunk in unique_chunks:
            if not raw_spans:
                raw_spans.append([chunk])
            else:
                last_span = raw_spans[-1]
                last_chunk = last_span[-1]
                if chunk["chunk_index"] == last_chunk["chunk_index"] + 1:
                    last_span.append(chunk)
                else:
                    raw_spans.append([chunk])

        for raw in raw_spans:
            # Anchor on the first originally-retrieved chunk in the span,
            # falling back to the leading chunk if none of them were originals
            # (shouldn't happen, but harmless).
            anchor = raw[0]
            for c in raw:
                if (current_document_id, c["chunk_index"]) in retrieved_by_index:
                    anchor = c
                    break

            content = "\n\n".join(c["content"] for c in raw)
            merged.append(
                {
                    "document_id": raw[0]["document_id"],
                    "document_title": raw[0].get("document_title", ""),
                    "document_url": raw[0].get("document_url"),
                    "document_content_path": raw[0].get("document_content_path"),
                    "source_type": anchor.get("source_type", "firstspirit"),
                    "content": content,
                    # Anchor-derived citation fields — these link back to the
                    # heading the user's query hit, not to a random neighbour.
                    "section_path": anchor.get("section_path") or [],
                    "anchor": anchor.get("anchor"),
                    "chunk_id": anchor.get("chunk_id") or anchor.get("id", ""),
                    "chunk_index": anchor.get("chunk_index", raw[0]["chunk_index"]),
                }
            )

    return merged

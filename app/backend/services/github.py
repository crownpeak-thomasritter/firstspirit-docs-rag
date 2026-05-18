"""GitHub Issues API client — used by the user-feedback route to file an
issue per "Report this answer" submission.

Design notes
------------

* Module-level singleton ``httpx.AsyncClient`` created lazily on first call.
  The default headers attach the GitHub API content negotiation + API
  version so individual call sites don't have to remember them.
* ``tenacity`` retry decorator built dynamically (mirrors
  :mod:`backend.services.crawler`) so tests can monkeypatch the wait knobs
  to zero and run retry paths instantly.
* Retries fire on 429, 5xx, ``httpx.TransportError`` and
  ``httpx.TimeoutException``. Auth failures (401/403) raise
  :class:`GitHubAuthError` immediately — retrying an invalid token only
  burns the rate limit. Validation errors (422) raise ``ValueError`` so the
  route handler can surface them as user-facing 502s.
* The issue body uses an HTML ``<pre>`` block for the Q/A panels (GitHub
  renders raw HTML in issue bodies, and ``<pre>`` is immune to Markdown
  injection) and a *variable-length* backtick fence around the user's
  correction so a correction containing ``\\`\\`\\``` doesn't break out
  of its fence.
"""

from __future__ import annotations

import html
import logging
import re

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.config import GITHUB_API_BASE_URL, GITHUB_MAX_RETRIES

logger = logging.getLogger(__name__)

GITHUB_TIMEOUT_SECONDS: float = 15.0
GITHUB_USER_AGENT: str = "firstspirit-docs-rag/1.0"

# Tenacity wait knobs — module-level floats so tests can monkeypatch them to
# zero. Mirrors the pattern in :mod:`backend.services.crawler`.
_RETRY_WAIT_MULTIPLIER: float = 1.0
_RETRY_WAIT_MAX_SECONDS: float = 30.0


class RetryableHTTPError(Exception):
    """Sentinel raised inside the retry boundary on 429/5xx responses."""


class GitHubAuthError(Exception):
    """Raised on 401/403 — never retried (invalid token = persistent failure)."""


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the lazily-initialised shared :class:`httpx.AsyncClient`."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(GITHUB_TIMEOUT_SECONDS),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": GITHUB_USER_AGENT,
            },
        )
    return _client


async def shutdown() -> None:
    """Close the shared client. Call from the FastAPI lifespan teardown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def _retry_decorator():
    """Build the tenacity decorator dynamically so test monkeypatching of
    :data:`_RETRY_WAIT_*` and :data:`GITHUB_MAX_RETRIES` takes effect at
    call time, not at import time.
    """
    return retry(
        stop=stop_after_attempt(max(1, GITHUB_MAX_RETRIES)),
        wait=wait_exponential(
            multiplier=_RETRY_WAIT_MULTIPLIER,
            min=0,
            max=_RETRY_WAIT_MAX_SECONDS,
        ),
        retry=retry_if_exception_type(
            (RetryableHTTPError, httpx.TransportError, httpx.TimeoutException)
        ),
        reraise=True,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_issue(
    *,
    repo: str,
    token: str,
    title: str,
    body: str,
    labels: list[str],
) -> str:
    """POST a new issue and return its ``html_url`` on success.

    Raises:
        GitHubAuthError: 401/403 from GitHub (invalid or insufficient token).
        ValueError: 422 validation failure (malformed payload, missing repo).
        RetryableHTTPError: persistent 429/5xx after the retry budget.
        httpx.HTTPError: transport-level failures the caller may want to log.
    """
    url = f"{GITHUB_API_BASE_URL}/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"title": title, "body": body, "labels": labels}

    @_retry_decorator()
    async def _do_request() -> httpx.Response:
        client = _get_client()
        resp = await client.post(url, headers=headers, json=payload)
        # Auth failures are NOT retried — invalid tokens don't get better
        # with time. Raise inside the retry boundary so tenacity skips them
        # (GitHubAuthError is not in the retry_if_exception_type list).
        if resp.status_code in (401, 403):
            raise GitHubAuthError(
                f"GitHub auth failed (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code == 422:
            raise ValueError(f"GitHub validation failed (HTTP 422): {resp.text[:500]}")
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            raise RetryableHTTPError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")
        return resp

    resp = await _do_request()
    if resp.status_code != 201:
        # Defensive: any non-2xx we didn't explicitly route above.
        raise RuntimeError(f"Unexpected GitHub response {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    issue_url = data.get("html_url")
    if not isinstance(issue_url, str) or not issue_url:
        raise RuntimeError("GitHub response missing html_url")
    return issue_url


# ---------------------------------------------------------------------------
# Body / title formatting
# ---------------------------------------------------------------------------


_BACKTICK_RUN_RE = re.compile(r"`+")


def _safe_fence_for(text: str) -> str:
    """Return a backtick fence at least one longer than the longest run of
    backticks in *text* (and at least 3) so the fenced block cannot be
    broken out of by a correction containing ``\\`\\`\\``` itself.
    """
    longest = 0
    for run in _BACKTICK_RUN_RE.findall(text):
        if len(run) > longest:
            longest = len(run)
    return "`" * max(3, longest + 1)


def _render_citations(sources: list[dict]) -> str:
    """Render the cited-sources list as Markdown bullets. Each entry uses
    ``url`` when available, else falls back to the vault content path.
    """
    if not sources:
        return "No citations on this answer."
    lines: list[str] = []
    for src in sources:
        title = src.get("title") or src.get("document_title") or "(untitled)"
        url = src.get("url") or src.get("document_url")
        content_path = src.get("content_path") or src.get("document_content_path")
        if url:
            lines.append(f"- [{title}]({url})")
        elif content_path:
            lines.append(f"- {title} (vault: `{content_path}`)")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)


def format_issue_body(
    *,
    question: str,
    answer: str,
    sources: list[dict],
    correction: str,
) -> str:
    """Produce the Markdown body for a feedback issue.

    Q/A panels use HTML ``<pre>`` blocks with HTML-escaped content so a
    malicious answer or question can't smuggle Markdown formatting. The
    correction uses a fenced code block sized to defeat triple-backtick
    injection.
    """
    short_q = re.sub(r"\s+", " ", question).strip()
    truncated_q = (short_q[:497].rstrip() + "…") if len(short_q) > 500 else short_q

    fence = _safe_fence_for(correction)
    citations_block = _render_citations(sources)

    return (
        "## User question\n\n"
        f"> {html.escape(truncated_q)}\n\n"
        "Full question:\n\n"
        f"<pre>{html.escape(question)}</pre>\n\n"
        "## Assistant answer\n\n"
        f"<pre>{html.escape(answer)}</pre>\n\n"
        "## Cited sources\n\n"
        f"{citations_block}\n\n"
        "## Suggested correction\n\n"
        f"{fence}\n{correction}\n{fence}\n"
    )


def truncate_title(question: str, max_len: int = 80) -> str:
    """Build the issue title from the user's question.

    Collapses whitespace, trims to ``max_len`` characters (with an ellipsis
    when truncated), and prefixes with ``"Answer feedback: "``. GitHub's
    title limit is 256 characters, so the prefix + max_len budget is
    well under the cap.
    """
    collapsed = re.sub(r"\s+", " ", question).strip() or "(no question)"
    if len(collapsed) > max_len:
        collapsed = collapsed[: max_len - 1].rstrip() + "…"
    return f"Answer feedback: {collapsed}"

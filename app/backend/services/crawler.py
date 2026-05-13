"""
Polite URL crawler — fetches one URL at a time with retry, backoff, robots.txt
respect, and ETag / Last-Modified conditional GETs.

The crawler is a pure fetcher: it returns raw bytes plus the headers needed to
mint a conditional re-fetch on the next sync. Parsing/extraction is the job of
``services.extractor``.

Design notes:
    - Single module-level ``httpx.AsyncClient`` — created on first ``fetch``,
      reused for every subsequent call so HTTP/2 connections + cookies are
      pooled. Callers should not close it; use :func:`shutdown` from the
      FastAPI lifespan handler if needed.
    - Retries via ``tenacity`` on HTTP 429 / 5xx and on network errors. The
      decorator uses ``reraise=True`` so the original ``httpx`` exception
      surfaces after the retry budget is exhausted.
    - ``robots.txt`` is fetched once per host and cached in-process. A
      disallowed URL short-circuits to ``CrawlStatus.SKIPPED_ROBOTS`` without
      ever issuing the real GET.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from urllib import robotparser
from urllib.parse import urlsplit, urlunsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.config import (
    CRAWLER_MAX_RETRIES,
    CRAWLER_REQUEST_DELAY_MS,
    CRAWLER_TIMEOUT_SECONDS,
    CRAWLER_USER_AGENT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CrawlStatus(str, Enum):
    """Result classification used by the ingester to decide what to do next."""

    OK = "ok"
    """Fresh content fetched. ``content`` is populated."""

    NOT_MODIFIED = "not_modified"
    """HTTP 304 — server confirmed the cached version is still valid."""

    SKIPPED_ROBOTS = "skipped_robots"
    """robots.txt forbids the configured User-Agent from reading this URL."""

    ERROR = "error"
    """All retries exhausted, or non-retryable status. See ``error``."""


@dataclass(frozen=True)
class CrawlResult:
    """Outcome of a single ``fetch`` call."""

    url: str
    status: CrawlStatus
    content: bytes | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    http_status: int | None = None
    error: str | None = None


class RetryableHTTPError(Exception):
    """Sentinel raised inside the retry boundary on 429/5xx responses."""


# ---------------------------------------------------------------------------
# Client + robots cache (module-level singletons)
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None
_robots_cache: dict[str, robotparser.RobotFileParser] = {}
_robots_lock = asyncio.Lock()


def _get_client() -> httpx.AsyncClient:
    """Return the lazily-initialised shared ``httpx.AsyncClient``."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(CRAWLER_TIMEOUT_SECONDS),
            headers={"User-Agent": CRAWLER_USER_AGENT},
            follow_redirects=True,
        )
    return _client


async def shutdown() -> None:
    """Close the shared client. Call from the FastAPI lifespan teardown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def _robots_url_for(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


async def _load_robots(host_key: str, robots_url: str) -> robotparser.RobotFileParser:
    """Fetch + parse robots.txt for one host. Failures degrade to allow-all."""
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    client = _get_client()
    try:
        resp = await client.get(robots_url)
    except (httpx.HTTPError, OSError) as exc:
        logger.info("robots.txt fetch failed for %s: %s — assuming allow-all", host_key, exc)
        rp.parse([])
        return rp

    if resp.status_code >= 400:
        # No robots.txt or unreadable — RFC 9309 says treat as allow-all.
        logger.info(
            "robots.txt returned HTTP %d for %s — assuming allow-all",
            resp.status_code,
            host_key,
        )
        rp.parse([])
        return rp

    rp.parse(resp.text.splitlines())
    return rp


async def _is_allowed_by_robots(url: str) -> bool:
    parts = urlsplit(url)
    host_key = f"{parts.scheme}://{parts.netloc}"
    async with _robots_lock:
        rp = _robots_cache.get(host_key)
        if rp is None:
            rp = await _load_robots(host_key, _robots_url_for(url))
            _robots_cache[host_key] = rp
    # urllib's RobotFileParser.can_fetch takes the *path* portion. Some
    # implementations accept the full URL; passing the full URL is safer
    # because urllib normalises it internally.
    return rp.can_fetch(CRAWLER_USER_AGENT, url)


def reset_robots_cache() -> None:
    """Drop the in-process robots.txt cache. Intended for tests."""
    _robots_cache.clear()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


_RETRY_WAIT_MULTIPLIER: float = 1.0
_RETRY_WAIT_MAX_SECONDS: float = 30.0


def _retry_decorator():
    """Build the tenacity decorator dynamically.

    Reads :data:`CRAWLER_MAX_RETRIES` plus the two ``_RETRY_WAIT_*`` knobs at
    call time, not at import time, so tests can monkey-patch them to make
    retry loops instant.
    """
    return retry(
        stop=stop_after_attempt(max(1, CRAWLER_MAX_RETRIES)),
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


async def _request_with_retry(url: str, headers: dict[str, str] | None) -> httpx.Response:
    """Issue a GET with retries on transient failures."""

    @_retry_decorator()
    async def _do_request() -> httpx.Response:
        client = _get_client()
        resp = await client.get(url, headers=headers)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            raise RetryableHTTPError(f"HTTP {resp.status_code} for {url}")
        return resp

    # ``tenacity``'s decorator return is typed ``Any``; cast to keep mypy happy.
    resp: httpx.Response = await _do_request()
    return resp


async def fetch(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> CrawlResult:
    """Fetch ``url`` politely.

    Args:
        url: Absolute http(s) URL.
        etag: Previously stored ``ETag`` header. If supplied, sent back as
            ``If-None-Match`` so the server can answer 304.
        last_modified: Previously stored ``Last-Modified`` header. If supplied,
            sent back as ``If-Modified-Since``.

    Returns:
        A :class:`CrawlResult`. Never raises for HTTP-level errors — they are
        encoded into ``status=ERROR`` with ``error`` and ``http_status``.
    """
    if not url.startswith(("http://", "https://")):
        return CrawlResult(url=url, status=CrawlStatus.ERROR, error="invalid_scheme")

    if not await _is_allowed_by_robots(url):
        logger.info("robots.txt disallows %s", url)
        return CrawlResult(url=url, status=CrawlStatus.SKIPPED_ROBOTS)

    # Polite throttle — applied before the GET so retries also pace themselves.
    if CRAWLER_REQUEST_DELAY_MS > 0:
        await asyncio.sleep(CRAWLER_REQUEST_DELAY_MS / 1000.0)

    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        resp = await _request_with_retry(url, headers or None)
    except (RetryableHTTPError, httpx.TransportError, httpx.TimeoutException) as exc:
        logger.warning("Crawler exhausted retries for %s: %s", url, exc)
        return CrawlResult(url=url, status=CrawlStatus.ERROR, error=str(exc))
    except httpx.HTTPError as exc:
        logger.warning("Crawler non-retryable error for %s: %s", url, exc)
        return CrawlResult(url=url, status=CrawlStatus.ERROR, error=str(exc))

    if resp.status_code == 304:
        return CrawlResult(
            url=str(resp.url),
            status=CrawlStatus.NOT_MODIFIED,
            http_status=304,
            etag=resp.headers.get("etag") or etag,
            last_modified=resp.headers.get("last-modified") or last_modified,
        )

    if resp.status_code >= 400:
        return CrawlResult(
            url=str(resp.url),
            status=CrawlStatus.ERROR,
            http_status=resp.status_code,
            error=f"HTTP {resp.status_code}",
        )

    return CrawlResult(
        url=str(resp.url),
        status=CrawlStatus.OK,
        content=resp.content,
        content_type=resp.headers.get("content-type"),
        etag=resp.headers.get("etag"),
        last_modified=resp.headers.get("last-modified"),
        http_status=resp.status_code,
    )

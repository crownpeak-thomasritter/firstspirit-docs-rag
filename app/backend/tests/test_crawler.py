"""Crawler tests — uses ``httpx.MockTransport`` to short-circuit the network.

Covers:
* 200 happy path
* 304 conditional GET
* 429-then-200 retry path
* 500 retries exhausted → ``CrawlStatus.ERROR``
* robots.txt disallow → ``CrawlStatus.SKIPPED_ROBOTS``
* invalid scheme → ``CrawlStatus.ERROR`` without any network call
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from backend.services import crawler


@pytest.fixture
def install_transport(monkeypatch):
    """Build a mock transport, install it on the module singleton.

    Yields a function that takes a single handler callable so each test can
    define its own routing logic.
    """

    installed: dict[str, httpx.AsyncClient] = {}

    def _install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": "test-agent"},
        )
        installed["client"] = client
        monkeypatch.setattr(crawler, "_client", client)
        # Disable real waits.
        monkeypatch.setattr(crawler, "_RETRY_WAIT_MULTIPLIER", 0.0)
        monkeypatch.setattr(crawler, "_RETRY_WAIT_MAX_SECONDS", 0.0)

    # Make sure robots.txt cache starts empty for every test.
    crawler.reset_robots_cache()
    yield _install
    crawler.reset_robots_cache()
    # The MockTransport-backed client holds no real connections, so we skip
    # aclose() — invoking it from a sync teardown fights pytest-asyncio's
    # event-loop lifecycle. ``monkeypatch`` will restore the original
    # ``crawler._client`` automatically.


def _robots_allow_all(request: httpx.Request) -> httpx.Response | None:
    """Default robots.txt handler — returns 404 (== allow all per RFC 9309)."""
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    return None


async def test_fetch_200_returns_ok(install_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        if (r := _robots_allow_all(request)) is not None:
            return r
        return httpx.Response(
            200,
            content=b"<html><body>hello</body></html>",
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"abc123"',
                "last-modified": "Wed, 21 Oct 2026 07:28:00 GMT",
            },
        )

    install_transport(handler)

    result = await crawler.fetch("https://example.com/docs/page")

    assert result.status is crawler.CrawlStatus.OK
    assert result.content == b"<html><body>hello</body></html>"
    assert result.content_type == "text/html; charset=utf-8"
    assert result.etag == '"abc123"'
    assert result.last_modified == "Wed, 21 Oct 2026 07:28:00 GMT"
    assert result.http_status == 200


async def test_fetch_304_returns_not_modified(install_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        if (r := _robots_allow_all(request)) is not None:
            return r
        # Honor If-None-Match
        if request.headers.get("if-none-match") == '"abc123"':
            return httpx.Response(304, headers={"etag": '"abc123"'})
        return httpx.Response(200, content=b"fresh")

    install_transport(handler)

    result = await crawler.fetch("https://example.com/docs/page", etag='"abc123"')

    assert result.status is crawler.CrawlStatus.NOT_MODIFIED
    assert result.http_status == 304
    assert result.content is None
    assert result.etag == '"abc123"'


async def test_fetch_429_then_200_retries(install_transport):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if (r := _robots_allow_all(request)) is not None:
            return r
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"})

    install_transport(handler)

    result = await crawler.fetch("https://example.com/docs/page")

    assert calls["n"] == 2
    assert result.status is crawler.CrawlStatus.OK
    assert result.content == b"ok"


async def test_fetch_500_exhausts_retries(install_transport, monkeypatch):
    # Lower the retry budget so the test is fast even with wait=0.
    monkeypatch.setattr(crawler, "CRAWLER_MAX_RETRIES", 2)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if (r := _robots_allow_all(request)) is not None:
            return r
        calls["n"] += 1
        return httpx.Response(500)

    install_transport(handler)

    result = await crawler.fetch("https://example.com/docs/page")

    assert result.status is crawler.CrawlStatus.ERROR
    assert calls["n"] == 2
    assert result.error is not None


async def test_fetch_404_is_non_retryable_error(install_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        if (r := _robots_allow_all(request)) is not None:
            return r
        return httpx.Response(404)

    install_transport(handler)

    result = await crawler.fetch("https://example.com/docs/page")

    assert result.status is crawler.CrawlStatus.ERROR
    assert result.http_status == 404


async def test_fetch_skipped_by_robots(install_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                content=b"User-agent: *\nDisallow: /docs/\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, content=b"should not be reached")

    install_transport(handler)

    result = await crawler.fetch("https://example.com/docs/secret")

    assert result.status is crawler.CrawlStatus.SKIPPED_ROBOTS
    assert result.content is None


async def test_fetch_invalid_scheme_returns_error():
    # No transport needed — this should short-circuit before any HTTP call.
    result = await crawler.fetch("file:///etc/passwd")
    assert result.status is crawler.CrawlStatus.ERROR
    assert result.error == "invalid_scheme"

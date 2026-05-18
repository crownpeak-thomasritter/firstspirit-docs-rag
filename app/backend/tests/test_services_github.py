"""Tests for :mod:`backend.services.github`.

Uses ``httpx.MockTransport`` to short-circuit the network — mirrors the
pattern from ``test_crawler.py``. Retry waits are forced to zero so retry
paths execute in microseconds.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from backend.services import github


@pytest.fixture
def install_transport(monkeypatch):
    """Install a mock-transport-backed client on the module singleton."""

    def _install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "test-agent",
            },
        )
        monkeypatch.setattr(github, "_client", client)
        # Force retry waits to zero so 5xx-retry tests run instantly.
        monkeypatch.setattr(github, "_RETRY_WAIT_MULTIPLIER", 0.0)
        monkeypatch.setattr(github, "_RETRY_WAIT_MAX_SECONDS", 0.0)

    yield _install


async def test_create_issue_201_returns_html_url(install_transport):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/owner/repo/issues/42", "number": 42},
        )

    install_transport(handler)

    url = await github.create_issue(
        repo="owner/repo",
        token="tk",
        title="t",
        body="b",
        labels=["feedback"],
    )

    assert url == "https://github.com/owner/repo/issues/42"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/repos/owner/repo/issues")
    assert captured["auth"] == "Bearer tk"


async def test_create_issue_500_then_201_retries_and_succeeds(install_transport):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(201, json={"html_url": "https://github.com/x/y/issues/1"})

    install_transport(handler)

    url = await github.create_issue(repo="x/y", token="tk", title="t", body="b", labels=[])
    assert calls["n"] == 2
    assert url == "https://github.com/x/y/issues/1"


async def test_create_issue_401_raises_GitHubAuthError_without_retry(install_transport):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text='{"message":"Bad credentials"}')

    install_transport(handler)

    with pytest.raises(github.GitHubAuthError):
        await github.create_issue(repo="x/y", token="bad", title="t", body="b", labels=[])
    assert calls["n"] == 1, "401 must not be retried"


async def test_create_issue_403_raises_GitHubAuthError_without_retry(install_transport):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text='{"message":"Forbidden"}')

    install_transport(handler)

    with pytest.raises(github.GitHubAuthError):
        await github.create_issue(repo="x/y", token="bad", title="t", body="b", labels=[])
    assert calls["n"] == 1


async def test_create_issue_invalid_repo_422_raises_ValueError(install_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text='{"message":"Validation Failed"}')

    install_transport(handler)

    with pytest.raises(ValueError, match="422"):
        await github.create_issue(repo="x/y", token="tk", title="t", body="b", labels=[])


async def test_create_issue_persistent_429_raises_retryable_after_budget(
    monkeypatch, install_transport
):
    monkeypatch.setattr(github, "GITHUB_MAX_RETRIES", 3)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text='{"message":"rate limited"}')

    install_transport(handler)

    with pytest.raises(github.RetryableHTTPError):
        await github.create_issue(repo="x/y", token="tk", title="t", body="b", labels=[])
    assert calls["n"] == 3, "must stop after GITHUB_MAX_RETRIES attempts, not loop forever"


async def test_create_issue_persistent_500_raises_after_budget(monkeypatch, install_transport):
    monkeypatch.setattr(github, "GITHUB_MAX_RETRIES", 2)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="upstream down")

    install_transport(handler)

    with pytest.raises(github.RetryableHTTPError):
        await github.create_issue(repo="x/y", token="tk", title="t", body="b", labels=[])
    assert calls["n"] == 2


async def test_create_issue_retries_transport_error_then_succeeds(install_transport):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("transient")
        return httpx.Response(201, json={"html_url": "https://github.com/x/y/issues/9"})

    install_transport(handler)

    url = await github.create_issue(repo="x/y", token="tk", title="t", body="b", labels=[])
    assert calls["n"] == 2
    assert url.endswith("/issues/9")


async def test_format_issue_body_wraps_correction_in_fence():
    body = github.format_issue_body(
        question="Q?",
        answer="A.",
        sources=[],
        correction="Plain correction text.",
    )
    assert "## Suggested correction" in body
    assert "```\nPlain correction text.\n```" in body
    assert "No citations on this answer." in body


async def test_format_issue_body_escapes_triple_backtick_correction():
    correction = "Look at this code:\n```python\nprint('hi')\n```"
    body = github.format_issue_body(question="Q?", answer="A.", sources=[], correction=correction)
    # Inner correction has a run of 3 backticks → outer fence must be ≥4.
    assert "````\n" + correction + "\n````" in body


async def test_format_issue_body_escapes_html_in_question_and_answer():
    body = github.format_issue_body(
        question="What about <script>alert(1)</script>?",
        answer="Use <b>bold</b> tags.",
        sources=[],
        correction="dont allow html",
    )
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;b&gt;bold&lt;/b&gt;" in body
    assert "<script>alert(1)</script>" not in body


async def test_format_issue_body_renders_url_and_content_path_citations():
    body = github.format_issue_body(
        question="Q?",
        answer="A.",
        sources=[
            {"title": "URL doc", "url": "https://docs.example/x"},
            {"title": "Vault doc", "content_path": "notes/x.md"},
        ],
        correction="correction here",
    )
    assert "[URL doc](https://docs.example/x)" in body
    assert "Vault doc (vault: `notes/x.md`)" in body


def test_truncate_title_under_80_chars_returns_full():
    title = github.truncate_title("Short question?")
    assert title == "Answer feedback: Short question?"


def test_truncate_title_over_80_chars_truncates_with_ellipsis_and_prefix():
    long_q = "a" * 200
    title = github.truncate_title(long_q)
    assert title.startswith("Answer feedback: ")
    payload = title[len("Answer feedback: ") :]
    assert len(payload) == 80
    assert payload.endswith("…")


def test_truncate_title_collapses_whitespace():
    title = github.truncate_title("How   do\n\nI\tdo X?")
    assert title == "Answer feedback: How do I do X?"


def test_truncate_title_empty_question_uses_placeholder():
    title = github.truncate_title("   ")
    assert title == "Answer feedback: (no question)"

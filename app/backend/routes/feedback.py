"""User feedback route — files a GitHub Issue per reported answer.

Surface: ``POST /api/feedback``. Body carries only ``message_id`` and
``suggested_correction`` — never the displayed Q/A. The handler
reconstructs the question + answer + citations from SQLite so a malicious
client cannot mislabel the report.

Flow:

1. 503 immediately if :data:`FEEDBACK_ENABLED` is off or the token is empty.
2. Look up the message; reject 404 if missing or not owned by the default
   user, 400 if it is not an assistant message.
3. Walk the message list backwards from the target to find the
   immediately-preceding ``role='user'`` message (the question).
4. Persist the feedback row with a JSON snapshot of the Q/A/citations
   **before** the GitHub call so the audit trail survives a GitHub outage.
5. Try to create the GitHub issue. On success, update the row to
   ``issue_filed`` with the URL. On any failure, update the row to
   ``issue_failed`` and raise 502.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.config import (
    FEEDBACK_ENABLED,
    FEEDBACK_GITHUB_REPO,
    FEEDBACK_GITHUB_TOKEN,
    FEEDBACK_MAX_CORRECTION_CHARS,
)
from backend.db import repository
from backend.routes.conversations import DEFAULT_USER_ID
from backend.services import github
from backend.services.github import GitHubAuthError

# Re-export the feature-flag constants as module attributes so test
# monkeypatching via ``monkeypatch.setattr(feedback, "FEEDBACK_ENABLED", ...)``
# can flip behaviour at call time. Python resolves a function's free names
# against its enclosing module's globals at the call site, so the route
# handler picks up the new value automatically.
__all__ = [
    "FEEDBACK_ENABLED",
    "FEEDBACK_GITHUB_REPO",
    "FEEDBACK_GITHUB_TOKEN",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter()


class FeedbackCreate(BaseModel):
    message_id: str = Field(..., min_length=1)
    suggested_correction: str = Field(..., min_length=10, max_length=FEEDBACK_MAX_CORRECTION_CHARS)

    @field_validator("suggested_correction", mode="before")
    @classmethod
    def correction_not_whitespace_only(cls, v: str) -> str:
        if isinstance(v, str) and len(v.strip()) < 10:
            raise ValueError("suggested_correction must have at least 10 non-whitespace characters")
        return v


def _build_payload(question: str, answer: str, sources: list[dict] | None) -> dict:
    citations: list[dict] = []
    for src in sources or []:
        citations.append(
            {
                "title": src.get("document_title") or src.get("title"),
                "url": src.get("document_url"),
                "content_path": src.get("document_content_path"),
            }
        )
    return {"question": question, "answer": answer, "citations": citations}


@router.post("/feedback")
async def create_feedback(body: FeedbackCreate) -> dict:
    if not FEEDBACK_ENABLED or not FEEDBACK_GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="Feedback is currently disabled")

    user_id = DEFAULT_USER_ID

    target = await repository.get_message_with_conversation(body.message_id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if target["role"] != "assistant":
        raise HTTPException(status_code=400, detail="Cannot report a user message")

    conv_id = target["conversation_id"]
    all_messages = await repository.list_messages(conv_id, user_id)

    target_idx: int | None = None
    for i, m in enumerate(all_messages):
        if m["id"] == body.message_id:
            target_idx = i
            break
    if target_idx is None:
        # Race: the message exists per the ownership lookup but is no
        # longer in the conversation. Treat as 404 to keep the surface clean.
        raise HTTPException(status_code=404, detail="Message not found in conversation")

    question_msg: dict | None = None
    for j in range(target_idx - 1, -1, -1):
        if all_messages[j]["role"] == "user":
            question_msg = all_messages[j]
            break
    if question_msg is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot report an assistant message with no preceding user question",
        )

    question = question_msg["content"]
    answer = all_messages[target_idx]["content"]
    sources = all_messages[target_idx].get("sources")
    payload = _build_payload(question, answer, sources)
    payload_json = json.dumps(payload)

    persisted = await repository.create_feedback(
        message_id=body.message_id,
        conversation_id=conv_id,
        user_id=user_id,
        suggested_correction=body.suggested_correction,
        payload_json=payload_json,
    )
    if persisted is None:
        # Lost the race between the ownership check and the insert.
        raise HTTPException(status_code=404, detail="Message not found")
    feedback_id: str = persisted["id"]

    title = github.truncate_title(question)
    issue_body = github.format_issue_body(
        question=question,
        answer=answer,
        sources=sources or [],
        correction=body.suggested_correction,
    )

    try:
        issue_url = await github.create_issue(
            repo=FEEDBACK_GITHUB_REPO,
            token=FEEDBACK_GITHUB_TOKEN,
            title=title,
            body=issue_body,
            labels=["feedback"],
        )
    except GitHubAuthError as exc:
        logger.error("GitHub auth failed when filing feedback issue: %s", exc)
        await repository.update_feedback_issue_url(
            feedback_id, github_issue_url=None, status="issue_failed"
        )
        raise HTTPException(
            status_code=502,
            detail="GitHub authentication failed — token is invalid",
        ) from exc
    except Exception as exc:
        logger.error("GitHub issue creation failed for feedback %s: %s", feedback_id, exc)
        await repository.update_feedback_issue_url(
            feedback_id, github_issue_url=None, status="issue_failed"
        )
        raise HTTPException(
            status_code=502,
            detail=f"GitHub issue creation failed: {exc}",
        ) from exc

    await repository.update_feedback_issue_url(
        feedback_id, github_issue_url=issue_url, status="issue_filed"
    )
    result = await repository.get_feedback_by_id(feedback_id)
    if result is None:  # pragma: no cover - guarded above
        raise HTTPException(status_code=500, detail="Failed to read persisted feedback")
    return result

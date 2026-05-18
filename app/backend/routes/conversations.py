"""Conversation management routes — FirstSpirit Docs RAG.

The pivot drops the donor's auth stack. Conversations are scoped to a single
synthetic user (``DEFAULT_USER_ID``) because the underlying repository layer
still carries ``user_id`` columns and parameters. The route handlers therefore
never read a current_user — the constant is the only identity anywhere in
the pivot's HTTP surface.

The ``/api/documents`` endpoint replaces the donor's ``/api/videos``: same
shape (return the public document catalog), different data source. Admin
listing with chunk counts and sync history lives on ``/api/sources/*``
(see :mod:`backend.routes.sources`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import FEEDBACK_ENABLED, FEEDBACK_GITHUB_TOKEN
from backend.db import repository

router = APIRouter()


# Single anonymous identity for the pivot — see module docstring.
DEFAULT_USER_ID = "default-user"

# Re-export so tests can monkeypatch ``conversations.FEEDBACK_ENABLED``
# / ``conversations.FEEDBACK_GITHUB_TOKEN`` and have ``get_conversation``
# pick up the new value on the next call.
__all__ = ["DEFAULT_USER_ID", "FEEDBACK_ENABLED", "FEEDBACK_GITHUB_TOKEN", "router"]


class ConversationCreate(BaseModel):
    title: str = "New Conversation"


class ConversationRename(BaseModel):
    title: str


@router.get("/conversations")
async def list_conversations() -> list[dict]:
    return await repository.list_conversations(user_id=DEFAULT_USER_ID)


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationCreate | None = None) -> dict:
    """Create a new empty conversation."""
    title = body.title if body else "New Conversation"
    return await repository.create_conversation(user_id=DEFAULT_USER_ID, title=title)


@router.get("/conversations/search")
async def search_conversations(q: str) -> list[dict]:
    """Title-contains search. Must be declared BEFORE /conversations/{conv_id}
    or FastAPI routes "search" to the path-parameter handler and returns 404.
    """
    return await repository.search_conversations_by_title(user_id=DEFAULT_USER_ID, query=q)


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str) -> dict:
    conv = await repository.get_conversation(conv_id, user_id=DEFAULT_USER_ID)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await repository.list_messages(conv_id, user_id=DEFAULT_USER_ID)
    feedback_enabled = bool(FEEDBACK_ENABLED and FEEDBACK_GITHUB_TOKEN)
    return {**conv, "messages": messages, "feedback_enabled": feedback_enabled}


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str) -> None:
    deleted = await repository.delete_conversation(conv_id, user_id=DEFAULT_USER_ID)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, body: ConversationRename) -> dict | None:
    updated = await repository.update_conversation_title(
        conv_id, user_id=DEFAULT_USER_ID, title=body.title
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await repository.get_conversation(conv_id, user_id=DEFAULT_USER_ID)


@router.get("/documents")
async def list_documents() -> list[dict]:
    """Public document catalog. Replaces the donor's ``/api/videos``."""
    return await repository.list_documents()

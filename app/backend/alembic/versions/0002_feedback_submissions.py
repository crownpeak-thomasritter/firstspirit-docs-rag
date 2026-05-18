"""Add feedback_submissions table for user-reported answer feedback.

Stores one row per "Report this answer" submission. ``payload_json`` is a
snapshot of the question + answer + citations at submit time so the audit
trail survives later edits/deletes of the underlying message rows.

* ``message_id`` FK with ON DELETE CASCADE → feedback is wiped when the
  reported assistant message is removed.
* ``conversation_id`` FK with ON DELETE CASCADE → same for the parent
  conversation. Storing it directly (instead of joining through messages)
  lets the LEFT JOIN in ``list_messages`` stay flat.
* ``status`` CHECK constraint enforces the three legal lifecycle values:
  ``submitted`` (row created, GitHub call not yet attempted),
  ``issue_filed`` (GitHub returned a URL), ``issue_failed`` (GitHub call
  exhausted retries or returned a non-retryable error).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-18

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_submissions (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            suggested_correction TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            github_issue_url TEXT,
            status TEXT NOT NULL DEFAULT 'submitted'
                CHECK (status IN ('submitted', 'issue_filed', 'issue_failed')),
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS feedback_submissions_message_id_idx "
        "ON feedback_submissions (message_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feedback_submissions")

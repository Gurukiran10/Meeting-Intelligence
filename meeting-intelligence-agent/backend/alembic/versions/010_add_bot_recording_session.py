"""add bot_recording_sessions table

Revision ID: 010_add_bot_recording_session
Revises: 009_add_google_oauth_fields
Create Date: 2026-04-28
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID

revision = "010_add_bot_recording_session"
down_revision = "009_add_google_oauth_fields"
branch_labels = None
depends_on = None

TABLE = "bot_recording_sessions"


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)

    if TABLE in inspector.get_table_names():
        return  # idempotent — safe to re-run

    op.create_table(
        TABLE,
        # ── Identity ──────────────────────────────────────────────────────────
        # UUID(as_uuid=True) maps to PostgreSQL native `uuid` type, which is
        # what meetings.id and users.id already are. Using String(36) here
        # produces `character varying`, and PostgreSQL rejects cross-type FKs.
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,         # Python-side default for ORM inserts
            nullable=False,
        ),
        sa.Column(
            "meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # ── Recording ─────────────────────────────────────────────────────────
        sa.Column("strategy", sa.String(32), nullable=False),        # "ffmpeg" | "playwright"
        sa.Column("audio_path", sa.String(512), nullable=True),      # local filesystem path
        sa.Column("audio_url", sa.String(512), nullable=True),       # cloud URL after upload
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        # ── Transcription ─────────────────────────────────────────────────────
        sa.Column("transcript_count", sa.Integer, nullable=True, default=0),
        # ── Lifecycle ─────────────────────────────────────────────────────────
        # started → recording → processing → completed | failed
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="started",   # DB-side default so INSERT without status works
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("recording_started_at", sa.DateTime, nullable=True),
        sa.Column("recording_stopped_at", sa.DateTime, nullable=True),
        sa.Column("processing_started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Composite index for the most common query pattern: all sessions for a user+meeting
    op.create_index(f"ix_{TABLE}_status", TABLE, ["status"])
    op.create_index(f"ix_{TABLE}_user_meeting", TABLE, ["user_id", "meeting_id"])


def downgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index(f"ix_{TABLE}_user_meeting", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_status", table_name=TABLE)
    op.drop_table(TABLE)

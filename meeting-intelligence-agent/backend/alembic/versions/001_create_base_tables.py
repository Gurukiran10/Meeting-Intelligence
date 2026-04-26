"""create base tables

Revision ID: 001_create_base_tables
Revises: (none - this is the initial migration)
Create Date: 2026-04-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "001_create_base_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    # ── organizations ────────────────────────────────────────────────
    if "organizations" not in tables:
        op.create_table(
            "organizations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)

    # ── users ────────────────────────────────────────────────────────
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("username", sa.String(length=100), nullable=False),
            sa.Column("full_name", sa.String(length=255), nullable=False),
            sa.Column("hashed_password", sa.String(length=255), nullable=False),
            sa.Column("avatar_url", sa.String(length=500), nullable=True),
            sa.Column("timezone", sa.String(length=50), nullable=True, server_default="UTC"),
            sa.Column("role", sa.String(length=50), nullable=True, server_default="member"),
            sa.Column("department", sa.String(length=100), nullable=True),
            sa.Column("job_title", sa.String(length=100), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_verified", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("is_superuser", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("preferences", sa.JSON(), nullable=True),
            sa.Column("notification_settings", sa.JSON(), nullable=True),
            sa.Column("integrations", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("last_login", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_users_organization_id"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email", name="uq_users_email"),
            sa.UniqueConstraint("username", name="uq_users_username"),
        )
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
        op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
        op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False)

    # ── meetings ─────────────────────────────────────────────────────
    if "meetings" not in tables:
        op.create_table(
            "meetings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("meeting_type", sa.String(length=50), nullable=True),
            sa.Column("platform", sa.String(length=50), nullable=True),
            sa.Column("external_id", sa.String(length=255), nullable=True),
            sa.Column("meeting_url", sa.String(length=500), nullable=True),
            sa.Column("scheduled_start", sa.DateTime(), nullable=False),
            sa.Column("scheduled_end", sa.DateTime(), nullable=False),
            sa.Column("actual_start", sa.DateTime(), nullable=True),
            sa.Column("actual_end", sa.DateTime(), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organizer_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("attendee_ids", sa.JSON(), nullable=True),
            sa.Column("attendee_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("recording_url", sa.String(length=500), nullable=True),
            sa.Column("recording_path", sa.String(length=500), nullable=True),
            sa.Column("recording_size_mb", sa.Float(), nullable=True),
            sa.Column("recording_consent", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("status", sa.String(length=50), nullable=True, server_default="scheduled"),
            sa.Column("transcription_status", sa.String(length=50), nullable=True, server_default="pending"),
            sa.Column("analysis_status", sa.String(length=50), nullable=True, server_default="pending"),
            sa.Column("agenda", sa.JSON(), nullable=True),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("key_decisions", sa.JSON(), nullable=True),
            sa.Column("discussion_topics", sa.JSON(), nullable=True),
            sa.Column("sentiment_score", sa.Float(), nullable=True),
            sa.Column("meeting_quality_score", sa.Float(), nullable=True),
            sa.Column("speaking_time", sa.JSON(), nullable=True),
            sa.Column("participation_score", sa.JSON(), nullable=True),
            sa.Column("interruption_count", sa.Integer(), nullable=True),
            sa.Column("silence_duration_minutes", sa.Float(), nullable=True),
            sa.Column("meeting_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_meetings_organization_id"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_meetings_created_by"),
            sa.ForeignKeyConstraint(["organizer_id"], ["users.id"], name="fk_meetings_organizer_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_meetings_organization_id"), "meetings", ["organization_id"], unique=False)
        op.create_index(op.f("ix_meetings_created_by"), "meetings", ["created_by"], unique=False)
        op.create_index(op.f("ix_meetings_scheduled_start"), "meetings", ["scheduled_start"], unique=False)
        op.create_index(op.f("ix_meetings_status"), "meetings", ["status"], unique=False)
        op.create_index(op.f("ix_meetings_external_id"), "meetings", ["external_id"], unique=False)

    # ── transcripts ──────────────────────────────────────────────────
    if "transcripts" not in tables:
        op.create_table(
            "transcripts",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("segment_number", sa.Integer(), nullable=False),
            sa.Column("speaker_id", sa.String(length=100), nullable=True),
            sa.Column("speaker_name", sa.String(length=255), nullable=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("language", sa.String(length=10), nullable=True, server_default="en"),
            sa.Column("start_time", sa.Float(), nullable=False),
            sa.Column("end_time", sa.Float(), nullable=False),
            sa.Column("duration", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("sentiment", sa.String(length=20), nullable=True),
            sa.Column("sentiment_score", sa.Float(), nullable=True),
            sa.Column("contains_question", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("contains_action_item", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("contains_decision", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("embedding_vector", sa.JSON(), nullable=True),
            sa.Column("transcript_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], name="fk_transcripts_meeting_id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_transcripts_user_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_transcripts_meeting_id"), "transcripts", ["meeting_id"], unique=False)

    # ── transcript_words ─────────────────────────────────────────────
    if "transcript_words" not in tables:
        op.create_table(
            "transcript_words",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("word", sa.String(length=255), nullable=False),
            sa.Column("start_time", sa.Float(), nullable=False),
            sa.Column("end_time", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], name="fk_transcript_words_transcript_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_transcript_words_transcript_id"), "transcript_words", ["transcript_id"], unique=False)

    # ── action_items ─────────────────────────────────────────────────
    if "action_items" not in tables:
        op.create_table(
            "action_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("context", sa.Text(), nullable=True),
            sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("collaborator_ids", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=True, server_default="open"),
            sa.Column("priority", sa.String(length=20), nullable=True, server_default="medium"),
            sa.Column("due_date", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("estimated_hours", sa.Float(), nullable=True),
            sa.Column("category", sa.String(length=100), nullable=True),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("blocked_by", sa.JSON(), nullable=True),
            sa.Column("blocks", sa.JSON(), nullable=True),
            sa.Column("extracted_from_text", sa.Text(), nullable=True),
            sa.Column("confidence_score", sa.Float(), nullable=True),
            sa.Column("extraction_method", sa.String(length=50), nullable=True),
            sa.Column("reminder_sent_48h", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("reminder_sent_24h", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("reminder_sent_overdue", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("reminder_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("external_task_id", sa.String(length=255), nullable=True),
            sa.Column("external_task_url", sa.String(length=500), nullable=True),
            sa.Column("integration_type", sa.String(length=50), nullable=True),
            sa.Column("sync_status", sa.String(length=50), nullable=True, server_default="pending"),
            sa.Column("item_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_action_items_organization_id"),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], name="fk_action_items_meeting_id"),
            sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], name="fk_action_items_assigned_to_user_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_action_items_organization_id"), "action_items", ["organization_id"], unique=False)
        op.create_index(op.f("ix_action_items_meeting_id"), "action_items", ["meeting_id"], unique=False)
        op.create_index(op.f("ix_action_items_assigned_to_user_id"), "action_items", ["assigned_to_user_id"], unique=False)
        op.create_index(op.f("ix_action_items_status"), "action_items", ["status"], unique=False)
        op.create_index(op.f("ix_action_items_due_date"), "action_items", ["due_date"], unique=False)

    # ── action_item_updates ──────────────────────────────────────────
    if "action_item_updates" not in tables:
        op.create_table(
            "action_item_updates",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("action_item_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("update_type", sa.String(length=50), nullable=False),
            sa.Column("old_value", sa.String(length=500), nullable=True),
            sa.Column("new_value", sa.String(length=500), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["action_item_id"], ["action_items.id"], name="fk_action_item_updates_action_item_id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_action_item_updates_user_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_action_item_updates_action_item_id"), "action_item_updates", ["action_item_id"], unique=False)

    # ── mentions ─────────────────────────────────────────────────────
    if "mentions" not in tables:
        op.create_table(
            "mentions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("mention_type", sa.String(length=50), nullable=False),
            sa.Column("mentioned_text", sa.Text(), nullable=False),
            sa.Column("context_before", sa.Text(), nullable=True),
            sa.Column("context_after", sa.Text(), nullable=True),
            sa.Column("full_context", sa.Text(), nullable=True),
            sa.Column("is_action_item", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("is_question", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("is_decision", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("is_feedback", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("relevance_score", sa.Float(), nullable=True),
            sa.Column("urgency_score", sa.Float(), nullable=True),
            sa.Column("sentiment", sa.String(length=20), nullable=True),
            sa.Column("sentiment_score", sa.Float(), nullable=True),
            sa.Column("detection_method", sa.String(length=50), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("notification_sent", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("notification_sent_at", sa.DateTime(), nullable=True),
            sa.Column("notification_type", sa.String(length=50), nullable=True),
            sa.Column("notification_read", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("notification_read_at", sa.DateTime(), nullable=True),
            sa.Column("user_responded", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("response_text", sa.Text(), nullable=True),
            sa.Column("response_at", sa.DateTime(), nullable=True),
            sa.Column("mention_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_mentions_organization_id"),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], name="fk_mentions_meeting_id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mentions_user_id"),
            sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], name="fk_mentions_transcript_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_mentions_organization_id"), "mentions", ["organization_id"], unique=False)
        op.create_index(op.f("ix_mentions_meeting_id"), "mentions", ["meeting_id"], unique=False)
        op.create_index(op.f("ix_mentions_user_id"), "mentions", ["user_id"], unique=False)

    # ── decisions ────────────────────────────────────────────────────
    if "decisions" not in tables:
        op.create_table(
            "decisions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("decision_text", sa.Text(), nullable=False),
            sa.Column("reasoning", sa.Text(), nullable=True),
            sa.Column("alternatives_considered", sa.JSON(), nullable=True),
            sa.Column("decision_type", sa.String(length=50), nullable=True),
            sa.Column("is_reversible", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("impact_level", sa.String(length=20), nullable=True),
            sa.Column("decision_maker_ids", sa.JSON(), nullable=True),
            sa.Column("affected_user_ids", sa.JSON(), nullable=True),
            sa.Column("affected_team", sa.String(length=100), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=True, server_default="decided"),
            sa.Column("implementation_deadline", sa.DateTime(), nullable=True),
            sa.Column("implemented_at", sa.DateTime(), nullable=True),
            sa.Column("expected_outcome", sa.Text(), nullable=True),
            sa.Column("actual_outcome", sa.Text(), nullable=True),
            sa.Column("outcome_met_expectation", sa.Boolean(), nullable=True),
            sa.Column("confidence_score", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], name="fk_decisions_meeting_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_decisions_meeting_id"), "decisions", ["meeting_id"], unique=False)


def downgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    for table in ["decisions", "mentions", "action_item_updates", "action_items",
                   "transcript_words", "transcripts", "meetings", "users", "organizations"]:
        if table in tables:
            # Drop indexes first (best effort)
            for idx in inspector.get_indexes(table):
                try:
                    op.drop_index(idx["name"], table_name=table)
                except Exception:
                    pass
            op.drop_table(table)

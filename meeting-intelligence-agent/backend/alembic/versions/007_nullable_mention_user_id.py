"""make mention user_id nullable

Revision ID: 007_nullable_mention_user_id
Revises: 006_add_notifications
Create Date: 2026-04-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "007_nullable_mention_user_id"
down_revision = "006_add_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    if "mentions" not in tables:
        return

    # Check if user_id is already nullable (e.g. from 001_create_base_tables)
    columns = inspector.get_columns("mentions")
    user_id_col = next((c for c in columns if c["name"] == "user_id"), None)
    if user_id_col and user_id_col.get("nullable", True):
        # Already nullable — nothing to do
        return

    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table("mentions", schema=None) as batch_op:
        batch_op.alter_column(
            "user_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    if "mentions" not in set(inspector.get_table_names()):
        return

    with op.batch_alter_table("mentions", schema=None) as batch_op:
        batch_op.alter_column(
            "user_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )

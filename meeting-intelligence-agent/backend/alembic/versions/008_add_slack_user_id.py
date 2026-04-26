"""add slack user id

Revision ID: 008_add_slack_user_id
Revises: 007_nullable_mention_user_id
Create Date: 2026-04-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "008_add_slack_user_id"
down_revision = "007_nullable_mention_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    
    if "users" not in inspector.get_table_names():
        return
        
    columns = [c["name"] for c in inspector.get_columns("users")]
    if "slack_user_id" not in columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("slack_user_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    
    if "users" not in inspector.get_table_names():
        return
        
    columns = [c["name"] for c in inspector.get_columns("users")]
    if "slack_user_id" in columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("slack_user_id")

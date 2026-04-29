"""add google oauth fields

Revision ID: 009_add_google_oauth_fields
Revises: 008_add_slack_user_id
Create Date: 2026-04-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "009_add_google_oauth_fields"
down_revision = "008_add_slack_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)

    if "users" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("users")}
    new_cols = []

    if "google_access_token" not in columns:
        new_cols.append(sa.Column("google_access_token", sa.String(length=2048), nullable=True))
    if "google_refresh_token" not in columns:
        new_cols.append(sa.Column("google_refresh_token", sa.String(length=512), nullable=True))
    if "google_token_expiry" not in columns:
        new_cols.append(sa.Column("google_token_expiry", sa.DateTime(), nullable=True))
    if "google_email" not in columns:
        new_cols.append(sa.Column("google_email", sa.String(length=255), nullable=True))
    if "google_connected" not in columns:
        new_cols.append(
            sa.Column("google_connected", sa.Boolean(), nullable=False, server_default="0")
        )

    if new_cols:
        with op.batch_alter_table("users", schema=None) as batch_op:
            for col in new_cols:
                batch_op.add_column(col)


def downgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)

    if "users" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("users")}
    to_drop = [
        c for c in (
            "google_access_token",
            "google_refresh_token",
            "google_token_expiry",
            "google_email",
            "google_connected",
        )
        if c in columns
    ]

    if to_drop:
        with op.batch_alter_table("users", schema=None) as batch_op:
            for col in to_drop:
                batch_op.drop_column(col)

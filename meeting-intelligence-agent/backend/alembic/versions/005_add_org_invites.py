"""add organization invites

Revision ID: 005_add_org_invites
Revises: 004_multi_tenant_saas_upgrade
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "005_add_org_invites"
down_revision = "004_multi_tenant_saas_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    if "invites" not in tables:
        op.create_table(
            "invites",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("token", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_invites_organization_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_invites_email"), "invites", ["email"], unique=False)
        op.create_index(op.f("ix_invites_organization_id"), "invites", ["organization_id"], unique=False)
        op.create_index(op.f("ix_invites_status"), "invites", ["status"], unique=False)
        op.create_index(op.f("ix_invites_token"), "invites", ["token"], unique=True)


def downgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    if "invites" not in set(inspector.get_table_names()):
        return

    op.drop_index(op.f("ix_invites_token"), table_name="invites")
    op.drop_index(op.f("ix_invites_status"), table_name="invites")
    op.drop_index(op.f("ix_invites_organization_id"), table_name="invites")
    op.drop_index(op.f("ix_invites_email"), table_name="invites")
    op.drop_table("invites")

"""multi tenant saas upgrade

Revision ID: 004_multi_tenant_saas_upgrade
Revises: 003_add_audit_and_retention
Create Date: 2026-04-15
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "004_multi_tenant_saas_upgrade"
down_revision = "003_add_audit_and_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    default_org_id = str(uuid.uuid4())
    tables = set(inspector.get_table_names())

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
        # Refresh table list after creation
        tables = set(inspector.get_table_names())

    existing_default_org = connection.execute(
        sa.text("SELECT id FROM organizations ORDER BY created_at ASC NULLS LAST, name ASC LIMIT 1")
    ).scalar()
    default_org_id = str(existing_default_org or default_org_id)

    if not existing_default_org:
        connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, slug, created_at) VALUES (:id, :name, :slug, CURRENT_TIMESTAMP)"
            ),
            {"id": default_org_id, "name": "Default Organization", "slug": "default-organization"},
        )

    # ── Users: add organization_id if missing ───────────────────────
    if "users" in tables:
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        if "organization_id" not in user_columns:
            with op.batch_alter_table("users") as batch_op:
                batch_op.add_column(sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
                batch_op.create_index(batch_op.f("ix_users_organization_id"), ["organization_id"], unique=False)
                batch_op.create_foreign_key("fk_users_organization_id", "organizations", ["organization_id"], ["id"])

        connection.execute(sa.text("UPDATE users SET organization_id = :org_id WHERE organization_id IS NULL"), {"org_id": default_org_id})

    # ── Meetings: add organization_id, created_by if missing ────────
    if "meetings" in tables:
        meeting_columns = {column["name"] for column in inspector.get_columns("meetings")}
        with op.batch_alter_table("meetings") as batch_op:
            if "organization_id" not in meeting_columns:
                batch_op.add_column(sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
                batch_op.create_index(batch_op.f("ix_meetings_organization_id"), ["organization_id"], unique=False)
                batch_op.create_foreign_key("fk_meetings_organization_id", "organizations", ["organization_id"], ["id"])
            if "created_by" not in meeting_columns:
                batch_op.add_column(sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True))
                batch_op.create_index(batch_op.f("ix_meetings_created_by"), ["created_by"], unique=False)
                batch_op.create_foreign_key("fk_meetings_created_by", "users", ["created_by"], ["id"])

        connection.execute(
            sa.text(
                """
                UPDATE meetings
                SET organization_id = COALESCE(
                    organization_id,
                    (SELECT users.organization_id FROM users WHERE users.id = meetings.organizer_id),
                    :org_id
                ),
                created_by = COALESCE(created_by, organizer_id)
                """
            ),
            {"org_id": default_org_id},
        )

    # ── Action items: add organization_id, assigned_to_user_id if missing
    if "action_items" in tables:
        action_item_columns = {column["name"] for column in inspector.get_columns("action_items")}
        with op.batch_alter_table("action_items") as batch_op:
            if "organization_id" not in action_item_columns:
                batch_op.add_column(sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
                batch_op.create_index(batch_op.f("ix_action_items_organization_id"), ["organization_id"], unique=False)
                batch_op.create_foreign_key("fk_action_items_organization_id", "organizations", ["organization_id"], ["id"])
            if "assigned_to_user_id" not in action_item_columns:
                batch_op.add_column(sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True))
                batch_op.create_index(batch_op.f("ix_action_items_assigned_to_user_id"), ["assigned_to_user_id"], unique=False)
                batch_op.create_foreign_key("fk_action_items_assigned_to_user_id", "users", ["assigned_to_user_id"], ["id"])

        # Only run data migration if there are rows AND if legacy owner_id column exists
        if "owner_id" in action_item_columns:
            connection.execute(
                sa.text(
                    """
                    UPDATE action_items
                    SET organization_id = COALESCE(
                        organization_id,
                        (SELECT meetings.organization_id FROM meetings WHERE meetings.id = action_items.meeting_id),
                        :org_id
                    ),
                    assigned_to_user_id = COALESCE(assigned_to_user_id, owner_id)
                    """
                ),
                {"org_id": default_org_id},
            )
        else:
            connection.execute(
                sa.text(
                    """
                    UPDATE action_items
                    SET organization_id = COALESCE(
                        organization_id,
                        (SELECT meetings.organization_id FROM meetings WHERE meetings.id = action_items.meeting_id),
                        :org_id
                    )
                    WHERE organization_id IS NULL
                    """
                ),
                {"org_id": default_org_id},
            )

    # ── Mentions: add organization_id if missing ────────────────────
    if "mentions" in tables:
        mention_columns = {column["name"] for column in inspector.get_columns("mentions")}
        if "organization_id" not in mention_columns:
            with op.batch_alter_table("mentions") as batch_op:
                batch_op.add_column(sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
                batch_op.create_index(batch_op.f("ix_mentions_organization_id"), ["organization_id"], unique=False)
                batch_op.create_foreign_key("fk_mentions_organization_id", "organizations", ["organization_id"], ["id"])

        connection.execute(
            sa.text(
                """
                UPDATE mentions
                SET organization_id = COALESCE(
                    organization_id,
                    (SELECT meetings.organization_id FROM meetings WHERE meetings.id = mentions.meeting_id),
                    :org_id
                )
                """
            ),
            {"org_id": default_org_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("mentions") as batch_op:
        batch_op.drop_constraint("fk_mentions_organization_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_mentions_organization_id"))
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("action_items") as batch_op:
        batch_op.drop_constraint("fk_action_items_assigned_to_user_id", type_="foreignkey")
        batch_op.drop_constraint("fk_action_items_organization_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_action_items_assigned_to_user_id"))
        batch_op.drop_index(batch_op.f("ix_action_items_organization_id"))
        batch_op.drop_column("assigned_to_user_id")
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("meetings") as batch_op:
        batch_op.drop_constraint("fk_meetings_created_by", type_="foreignkey")
        batch_op.drop_constraint("fk_meetings_organization_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_meetings_created_by"))
        batch_op.drop_index(batch_op.f("ix_meetings_organization_id"))
        batch_op.drop_column("created_by")
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_organization_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_users_organization_id"))
        batch_op.drop_column("organization_id")

    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_table("organizations")

"""Create audit_log, notification_idempotency, and retention_policy tables

Revision ID: 003_add_audit_and_retention
Revises: 002_initial_models
Create Date: 2026-04-11 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003_add_audit_and_retention'
down_revision = '002_initial_models'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('resource_type', sa.String(50), nullable=False),
        sa.Column('resource_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('old_value', sa.JSON(), nullable=True),
        sa.Column('new_value', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='success'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_resource_id'), 'audit_logs', ['resource_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_resource_type'), 'audit_logs', ['resource_type'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)

    # Create notification_idempotencies table
    op.create_table(
        'notification_idempotencies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('idempotency_key', sa.String(255), nullable=False),
        sa.Column('notification_type', sa.String(100), nullable=False),
        sa.Column('meeting_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('recipient', sa.String(255), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('response_status', sa.String(20), nullable=True),
        sa.Column('response_body', sa.String(500), nullable=True),
        sa.Column('retry_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_idempotency_key')
    )
    op.create_index(op.f('ix_notification_idempotencies_created_at'), 'notification_idempotencies', ['created_at'], unique=False)
    op.create_index(op.f('ix_notification_idempotencies_idempotency_key'), 'notification_idempotencies', ['idempotency_key'], unique=False)
    op.create_index(op.f('ix_notification_idempotencies_meeting_id'), 'notification_idempotencies', ['meeting_id'], unique=False)
    op.create_index(op.f('ix_notification_idempotencies_notification_type'), 'notification_idempotencies', ['notification_type'], unique=False)

    # Create retention_policies table
    op.create_table(
        'retention_policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('applies_to_type', sa.String(50), nullable=False),
        sa.Column('recording_retention_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('transcript_retention_days', sa.Integer(), nullable=False, server_default='90'),
        sa.Column('analysis_retention_days', sa.Integer(), nullable=False, server_default='90'),
        sa.Column('audit_log_retention_days', sa.Integer(), nullable=False, server_default='365'),
        sa.Column('notification_log_retention_days', sa.Integer(), nullable=False, server_default='90'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('auto_delete_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('require_approval_before_delete', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_sensitive', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('sensitive_multiplier', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )

    # Create retention_logs table
    op.create_table(
        'retention_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('resource_type', sa.String(50), nullable=False),
        sa.Column('resource_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resource_name', sa.String(500), nullable=True),
        sa.Column('meeting_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reason', sa.String(100), nullable=False),
        sa.Column('policy_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('deleted_by', sa.String(50), nullable=True),
        sa.Column('data_size_mb', sa.Integer(), nullable=True),
        sa.Column('checksum_before_delete', sa.String(64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_retention_logs_deleted_at'), 'retention_logs', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_retention_logs_meeting_id'), 'retention_logs', ['meeting_id'], unique=False)
    op.create_index(op.f('ix_retention_logs_resource_type'), 'retention_logs', ['resource_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_retention_logs_resource_type'), table_name='retention_logs')
    op.drop_index(op.f('ix_retention_logs_meeting_id'), table_name='retention_logs')
    op.drop_index(op.f('ix_retention_logs_deleted_at'), table_name='retention_logs')
    op.drop_table('retention_logs')
    
    op.drop_table('retention_policies')
    
    op.drop_index(op.f('ix_notification_idempotencies_notification_type'), table_name='notification_idempotencies')
    op.drop_index(op.f('ix_notification_idempotencies_meeting_id'), table_name='notification_idempotencies')
    op.drop_index(op.f('ix_notification_idempotencies_idempotency_key'), table_name='notification_idempotencies')
    op.drop_index(op.f('ix_notification_idempotencies_created_at'), table_name='notification_idempotencies')
    op.drop_table('notification_idempotencies')
    
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_resource_type'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_resource_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')

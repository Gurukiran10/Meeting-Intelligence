"""
Retention Policy Model - Enforce data retention and auto-deletion
"""
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, JSON, Integer, Text, Boolean
import uuid

from app.core.database import Base
from app.models.types import GUID


class RetentionPolicy(Base):
    """Retention policy configuration and enforcement"""
    __tablename__ = "retention_policies"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    
    # Policy details
    name = Column(String(100), nullable=False)
    # Examples: "default_7_days", "client_meetings_90_days", "internal_30_days"
    
    description = Column(Text)
    
    # What to apply to
    applies_to_type = Column(String(50), nullable=False)
    # Examples: "all_meetings", "meeting_type:client", "tag:confidential"
    
    # Retention rules
    recording_retention_days = Column(Integer, default=30)
    transcript_retention_days = Column(Integer, default=90)
    analysis_retention_days = Column(Integer, default=90)
    audit_log_retention_days = Column(Integer, default=365)
    notification_log_retention_days = Column(Integer, default=90)
    
    # Enforcement
    is_active = Column(Boolean, default=True)
    auto_delete_enabled = Column(Boolean, default=True)
    require_approval_before_delete = Column(Boolean, default=False)
    
    # Exceptions (for sensitive meetings)
    is_sensitive = Column(Boolean, default=False)
    sensitive_multiplier = Column(Integer, default=3)  # 3x longer retention for sensitive
    
    # Tracking
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<RetentionPolicy {self.name}>"


class RetentionLog(Base):
    """Log of deletion actions for compliance"""
    __tablename__ = "retention_logs"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    
    # What was deleted
    resource_type = Column(String(50), nullable=False, index=True)
    # Examples: recording, transcript, audio_file
    
    resource_id = Column(GUID(), index=True)
    resource_name = Column(String(500))
    
    meeting_id = Column(GUID(), index=True)
    
    # Why it was deleted
    reason = Column(String(100), nullable=False)
    # Examples: retention_policy_enforcement, requested_deletion, consent_withdrawal
    
    policy_id = Column(GUID())
    
    # Details
    deleted_by = Column(String(50))  # "system", "user:{user_id}", "admin:{user_id}"
    data_size_mb = Column(Integer)
    
    # Verification
    checksum_before_delete = Column(String(64))  # SHA256 for verification
    
    # Timestamp
    deleted_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    def __repr__(self):
        return f"<RetentionLog deleted {self.resource_type}:{self.resource_id}>"

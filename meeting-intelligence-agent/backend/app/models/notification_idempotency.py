"""
Notification Idempotency Model - Prevent duplicate Slack/email notifications
"""
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Boolean, Integer
from sqlalchemy.orm import relationship
import uuid

from app.core.database import Base
from app.models.types import GUID


class NotificationIdempotency(Base):
    """Track sent notifications to prevent duplicates"""
    __tablename__ = "notification_idempotencies"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    
    # Unique identifier to prevent duplicate notification
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    # Format: "{meeting_id}:{notification_type}:{timestamp}"
    # Example: "550e8400-e29b-41d4-a716-446655440000:slack_summary:2026-04-09T10:00:00"
    
    # What notification
    notification_type = Column(String(100), nullable=False, index=True)
    # Examples: slack_summary, email_summary, mention_alert, action_reminder, overdue_escalation
    
    meeting_id = Column(GUID(), ForeignKey("meetings.id"), index=True)
    
    # Delivery details
    provider = Column(String(50), nullable=False)  # slack, email, internal
    recipient = Column(String(255), nullable=False)  # Channel ID, email, user ID
    
    # Payload for debugging
    payload_hash = Column(String(64))  # SHA256 hash of notification payload
    payload = Column(JSON)
    
    # Status
    status = Column(String(50), default="pending")  # pending, sent, failed, retrying
    attempt_count = Column(Integer, default=0)
    
    # Results
    sent_at = Column(DateTime)
    response_status = Column(String(20))  # 200, 401, 429, 500, etc
    response_body = Column(String(500))
    retry_at = Column(DateTime)
    
    # Tracking
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    meeting = relationship("Meeting", foreign_keys=[meeting_id])
    
    def __repr__(self):
        return f"<Notification {self.notification_type} to {self.recipient} status={self.status}>"

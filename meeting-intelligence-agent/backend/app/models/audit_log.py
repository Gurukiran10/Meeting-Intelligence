"""
Audit Log Model - Track all critical actions for compliance and debugging
"""
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, Text, ForeignKey
from sqlalchemy.orm import relationship
import uuid

from app.core.database import Base
from app.models.types import GUID


class AuditLog(Base):
    """Audit log for tracking all critical actions"""
    __tablename__ = "audit_logs"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    
    # Who did it
    user_id = Column(GUID(), ForeignKey("users.id"), index=True)
    
    # What did they do
    action = Column(String(100), nullable=False, index=True)
    # Examples: meeting_created, transcript_uploaded, summary_generated, meeting_reprocessed, 
    # recording_deleted, notification_sent, decision_updated, action_item_completed
    
    # What resource
    resource_type = Column(String(50), nullable=False, index=True)
    # Examples: meeting, transcript, notification, action_item, decision
    
    resource_id = Column(GUID(), index=True)
    
    # What happened
    description = Column(Text)
    old_value = Column(JSON)  # For updates: what it was before
    new_value = Column(JSON)  # For updates: what it is now
    
    # Request context
    ip_address = Column(String(45))  # IPv4 or IPv6
    user_agent = Column(String(500))
    
    # Status
    status = Column(String(50), default="success")  # success, failure, partial
    error_message = Column(Text)  # If failed
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    
    def __repr__(self):
        return f"<AuditLog {self.action} on {self.resource_type}:{self.resource_id}>"

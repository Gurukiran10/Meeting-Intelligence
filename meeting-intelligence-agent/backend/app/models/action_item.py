"""
Action Item Model
"""
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Integer, Float, JSON
from sqlalchemy.orm import relationship, synonym
import uuid

from app.core.database import Base
from app.models.types import GUID


class ActionItem(Base):
    """Action item model"""
    __tablename__ = "action_items"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    organization_id = Column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    meeting_id = Column(GUID(), ForeignKey("meetings.id"), nullable=False, index=True)
    
    # Content
    title = Column(String(500), nullable=False)
    description = Column(Text)
    context = Column(Text)  # Surrounding conversation
    
    # Assignment
    assigned_to_user_id = Column(GUID(), ForeignKey("users.id"), index=True)
    owner_id = synonym("assigned_to_user_id")
    collaborator_ids = Column(JSON, default=[])  # List of UUID strings
    
    # Status
    status = Column(String(50), default="open", index=True)
    # open, in_progress, blocked, completed, cancelled
    priority = Column(String(20), default="medium")  # low, medium, high, urgent
    
    # Timing
    due_date = Column(DateTime, index=True)
    completed_at = Column(DateTime)
    estimated_hours = Column(Float)
    
    # Classification
    category = Column(String(100))  # research, development, review, meeting, etc
    tags = Column(JSON, default=[])
    
    # Dependencies
    blocked_by = Column(JSON, default=[])  # List of action item ID strings
    blocks = Column(JSON, default=[])
    
    # Extraction Info
    extracted_from_text = Column(Text)
    confidence_score = Column(Float)  # 0 to 1
    extraction_method = Column(String(50))  # manual, ai_detected, explicit
    
    # Reminders
    reminder_sent_48h = Column(Boolean, default=False)
    reminder_sent_24h = Column(Boolean, default=False)
    reminder_sent_overdue = Column(Boolean, default=False)
    reminder_count = Column(Integer, default=0)
    
    # Integration
    external_task_id = Column(String(255))  # Linear, Jira, Asana, etc
    external_task_url = Column(String(500))
    integration_type = Column(String(50))  # linear, jira, asana, notion
    sync_status = Column(String(50), default="pending")
    
    # Metadata
    item_metadata = Column(JSON, default={})
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)
    
    # Relationships
    organization = relationship("Organization", back_populates="action_items")
    meeting = relationship("Meeting", back_populates="action_items")
    assigned_to_user = relationship("User", back_populates="action_items", foreign_keys=[assigned_to_user_id])
    updates = relationship("ActionItemUpdate", back_populates="action_item", cascade="all, delete-orphan")

    @property
    def owner(self):
        return self.assigned_to_user

    @owner.setter
    def owner(self, value):
        self.assigned_to_user = value


class ActionItemUpdate(Base):
    """Action item update/comment history"""
    __tablename__ = "action_item_updates"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    action_item_id = Column(GUID(), ForeignKey("action_items.id"), nullable=False, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    
    update_type = Column(String(50), nullable=False)  # status_change, comment, assigned, completed
    old_value = Column(String(500))
    new_value = Column(String(500))
    comment = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    action_item = relationship("ActionItem", back_populates="updates")

"""
User Model
"""
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey
from sqlalchemy.orm import relationship
import uuid

from app.core.database import Base
from app.models.types import GUID


class User(Base):
    """User model"""
    __tablename__ = "users"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    organization_id = Column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    slack_user_id = Column(String(255), nullable=True)
    
    # Profile
    avatar_url = Column(String(500))
    timezone = Column(String(50), default="UTC")
    role = Column(String(50), default="member")  # admin, member
    department = Column(String(100))
    job_title = Column(String(100))
    
    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    is_superuser = Column(Boolean, default=False)
    
    # Preferences
    preferences = Column(JSON, default={})
    notification_settings = Column(JSON, default={
        "email_enabled": True,              # Receive email notifications
        "slack_enabled": True,              # Receive Slack notifications
        "real_time_mentions": True,         # Real-time mention alerts
        "daily_digest": True,               # Daily digest summary
        "action_reminders": True,           # Action item reminders
        "mention_confidence_threshold": 0.7,# Only alert if confidence >= threshold
        "digest_time": "08:00",            # Time of day for digest (HH:MM)
        "alert_channels": ["slack", "email"], # Channels for alerts
    })
    
    # Google OAuth
    google_access_token = Column(String(2048), nullable=True)
    google_refresh_token = Column(String(512), nullable=True)
    google_token_expiry = Column(DateTime, nullable=True)
    google_email = Column(String(255), nullable=True)
    google_connected = Column(Boolean, default=False)

    # Integrations
    integrations = Column(JSON, default={})
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime)
    
    # Relationships
    organization = relationship("Organization", back_populates="users")
    meetings = relationship("Meeting", back_populates="organizer", foreign_keys="Meeting.organizer_id")
    action_items = relationship("ActionItem", back_populates="assigned_to_user", foreign_keys="ActionItem.assigned_to_user_id")
    mentions = relationship("Mention", back_populates="user")
    notifications = relationship("Notification", back_populates="user")

"""
Internal user notification model
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, Boolean
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.types import GUID


class Notification(Base):
    """In-app notification for a user inside an organization."""
    __tablename__ = "notifications"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False, index=True)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    notification_metadata = Column(JSON, default={})

    user = relationship("User", back_populates="notifications")
    organization = relationship("Organization", back_populates="notifications")

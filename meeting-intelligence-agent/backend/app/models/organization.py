"""
Organization / Tenant Model
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.types import GUID


class Organization(Base):
    """Tenant / workspace model"""
    __tablename__ = "organizations"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(120), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = relationship("User", back_populates="organization")
    meetings = relationship("Meeting", back_populates="organization")
    action_items = relationship("ActionItem", back_populates="organization")
    mentions = relationship("Mention", back_populates="organization")
    invites = relationship("Invite", back_populates="organization")
    notifications = relationship("Notification", back_populates="organization")

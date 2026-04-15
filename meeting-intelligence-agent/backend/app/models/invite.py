"""
Organization invite model
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.types import GUID


class Invite(Base):
    """Invitation for a user to join an organization."""
    __tablename__ = "invites"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    organization_id = Column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    token = Column(String(255), nullable=False, unique=True, index=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    organization = relationship("Organization", back_populates="invites")

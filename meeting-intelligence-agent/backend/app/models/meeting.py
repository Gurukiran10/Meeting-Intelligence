"""
Meeting Model
"""
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, Integer, Text, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
import uuid

from app.core.database import Base
from app.models.types import GUID


class Meeting(Base):
    """Meeting model"""
    __tablename__ = "meetings"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    organization_id = Column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    
    # Basic Info
    title = Column(String(500), nullable=False)
    description = Column(Text)
    meeting_type = Column(String(50))  # internal, client, standup, review
    platform = Column(String(50))  # zoom, teams, meet, manual
    external_id = Column(String(255), index=True)  # Platform meeting ID
    meeting_url = Column(String(500))
    
    # Scheduling
    scheduled_start = Column(DateTime, nullable=False, index=True)
    scheduled_end = Column(DateTime, nullable=False)
    actual_start = Column(DateTime)
    actual_end = Column(DateTime)
    duration_minutes = Column(Integer)
    
    # Participants
    created_by = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    organizer_id = Column(GUID(), ForeignKey("users.id"))
    attendee_ids = Column(JSON, default=[])  # List of user ID strings
    attendee_count = Column(Integer, default=0)
    
    # Recording
    recording_url = Column(String(500))
    recording_path = Column(String(500))
    recording_size_mb = Column(Float)
    recording_consent = Column(Boolean, default=False)
    
    # Processing Status
    status = Column(String(50), default="scheduled", index=True)
    # scheduled        — created, bot not yet triggered
    # joining          — bot is actively attempting to join
    # waiting_for_host — bot joined but was alone; retry scheduled
    # host_absent      — max retries reached, host never showed
    # in_progress      — bot confirmed inside meeting, recording active
    # transcribing     — recording saved, transcription running
    # analyzing        — transcription done, NLP running
    # completed        — all processing done
    # failed           — bot or processing failed permanently
    # bot_rejected     — host denied bot admission
    transcription_status = Column(String(50), default="pending")
    analysis_status = Column(String(50), default="pending")
    
    # Content
    agenda = Column(JSON)
    tags = Column(JSON, default=[])  # List of tag strings
    
    # AI Analysis
    summary = Column(Text)
    key_decisions = Column(JSON)
    discussion_topics = Column(JSON)
    sentiment_score = Column(Float)  # -1 to 1
    meeting_quality_score = Column(Float)  # 0 to 100
    
    # Analytics
    speaking_time = Column(JSON)  # {user_id: minutes}
    participation_score = Column(JSON)  # {user_id: score}
    interruption_count = Column(Integer)
    silence_duration_minutes = Column(Float)
    
    # Metadata
    meeting_metadata = Column(JSON, default={})
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)  # Soft delete
    
    # Relationships
    organization = relationship("Organization", back_populates="meetings")
    organizer = relationship("User", back_populates="meetings", foreign_keys=[organizer_id])
    transcripts = relationship("Transcript", back_populates="meeting", cascade="all, delete-orphan")
    action_items = relationship("ActionItem", back_populates="meeting", cascade="all, delete-orphan")
    mentions = relationship("Mention", back_populates="meeting", cascade="all, delete-orphan")
    decisions = relationship("Decision", back_populates="meeting", cascade="all, delete-orphan")

"""
Mention Model
"""
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Float, Integer, JSON
from sqlalchemy.orm import relationship
import uuid

from app.core.database import Base
from app.models.types import GUID


class Mention(Base):
    """User mention detection model"""
    __tablename__ = "mentions"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    organization_id = Column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    meeting_id = Column(GUID(), ForeignKey("meetings.id"), nullable=False, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    transcript_id = Column(GUID(), ForeignKey("transcripts.id"))
    
    # Mention Type
    mention_type = Column(String(50), nullable=False)
    # direct, contextual, action_assignment, question, feedback, decision_impact
    
    # Content
    mentioned_text = Column(Text, nullable=False)
    context_before = Column(Text)
    context_after = Column(Text)
    full_context = Column(Text)
    
    # Classification
    is_action_item = Column(Boolean, default=False)
    is_question = Column(Boolean, default=False)
    is_decision = Column(Boolean, default=False)
    is_feedback = Column(Boolean, default=False)
    
    # Scoring
    relevance_score = Column(Float)  # 0 to 100
    urgency_score = Column(Float)  # 0 to 100
    sentiment = Column(String(20))  # positive, negative, neutral
    sentiment_score = Column(Float)  # -1 to 1
    
    # Detection
    detection_method = Column(String(50))  # direct_name, contextual_ai, project_related
    confidence = Column(Float)  # 0 to 1
    
    # Notification
    notification_sent = Column(Boolean, default=False)
    notification_sent_at = Column(DateTime)
    notification_type = Column(String(50))  # slack, email, push
    notification_read = Column(Boolean, default=False)
    notification_read_at = Column(DateTime)
    
    # Response
    user_responded = Column(Boolean, default=False)
    response_text = Column(Text)
    response_at = Column(DateTime)
    
    # Metadata
    mention_metadata = Column(JSON, default={})
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    organization = relationship("Organization", back_populates="mentions")
    meeting = relationship("Meeting", back_populates="mentions")
    user = relationship("User", back_populates="mentions")
    transcript = relationship("Transcript", back_populates="mentions")


class Decision(Base):
    """Meeting decision tracking"""
    __tablename__ = "decisions"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(GUID(), ForeignKey("meetings.id"), nullable=False, index=True)
    
    # Decision Content
    decision_text = Column(Text, nullable=False)
    reasoning = Column(Text)
    alternatives_considered = Column(JSON)
    
    # Classification
    decision_type = Column(String(50))  # strategic, tactical, procedural, resource
    is_reversible = Column(Boolean, default=True)
    impact_level = Column(String(20))  # low, medium, high, critical
    
    # Ownership
    decision_maker_ids = Column(JSON)  # Array of user IDs
    affected_user_ids = Column(JSON)
    affected_team = Column(String(100))
    
    # Execution
    status = Column(String(50), default="decided")  # decided, implementing, completed, reversed
    implementation_deadline = Column(DateTime)
    implemented_at = Column(DateTime)
    
    # Outcomes
    expected_outcome = Column(Text)
    actual_outcome = Column(Text)
    outcome_met_expectation = Column(Boolean)
    
    # Scoring
    confidence_score = Column(Float)  # 0 to 1
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    meeting = relationship("Meeting", back_populates="decisions")

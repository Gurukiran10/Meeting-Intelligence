"""
Meetings API Endpoints
"""
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, cast, String, or_
from pydantic import BaseModel, Field, field_validator, ConfigDict

from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.action_item import ActionItem
from app.models.mention import Mention
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user, require_org_member
from app.tasks.meeting_processor import process_meeting_recording_background
from app.services.absence_management import absence_management_service
from app.services.pre_meeting_briefs import pre_meeting_brief_service

router = APIRouter()


def _resolve_attendee_ids(db: Session, attendee_inputs: List[str]) -> List[str]:
    resolved: List[str] = []
    seen: set[str] = set()

    for raw_value in attendee_inputs:
        value = raw_value.strip()
        if not value:
            continue

        normalized_value = value
        try:
            normalized_value = str(UUID(value))
        except ValueError:
            user = db.execute(
                select(User).where(
                    or_(
                        User.email.ilike(value),
                        User.username.ilike(value),
                    )
                )
            ).scalar_one_or_none()
            if user is not None:
                normalized_value = str(user.id)

        if normalized_value not in seen:
            seen.add(normalized_value)
            resolved.append(normalized_value)

    return resolved


def _user_attendee_tokens(user: User) -> List[str]:
    tokens = [
        str(getattr(user, "id", "") or "").strip(),
        str(getattr(user, "email", "") or "").strip(),
        str(getattr(user, "username", "") or "").strip(),
    ]
    return [token for token in tokens if token]


def _is_admin(user: User) -> bool:
    return str(getattr(user, "role", "") or "").lower() == "admin"


def _can_access_meeting(user: User, meeting: Meeting) -> bool:
    if str(getattr(meeting, "organization_id", "") or "") != str(getattr(user, "organization_id", "") or ""):
        return False
    if _is_admin(user):
        return True
    attendee_tokens = set(_user_attendee_tokens(user))
    return (
        meeting.organizer_id == user.id
        or getattr(meeting, "created_by", None) == getattr(user, "id", None)
        or bool(attendee_tokens.intersection(set(meeting.attendee_ids or [])))
    )


class MeetingCreate(BaseModel):
    title: str
    description: Optional[str] = None
    meeting_type: Optional[str] = None
    platform: str = "manual"
    scheduled_start: datetime
    scheduled_end: datetime
    attendee_ids: List[str] = Field(default_factory=list)
    agenda: Optional[Union[List[str], Dict[str, Any]]] = None
    tags: List[str] = Field(default_factory=list)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"zoom", "manual"}:
            raise ValueError("Platform must be either 'zoom' or 'manual'")
        return normalized

    @field_validator("attendee_ids", mode="before")
    @classmethod
    def normalize_attendees(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("attendee_ids must be a list")

        normalized: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @field_validator("agenda", mode="before")
    @classmethod
    def normalize_agenda(cls, value: Any) -> Optional[Union[List[str], Dict[str, Any]]]:
        if value is None:
            return None
        if isinstance(value, list):
            topics = [str(item).strip() for item in value if str(item).strip()]
            return topics or None
        if isinstance(value, dict):
            return value
        raise ValueError("agenda must be a list of topics or an object")

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tags must be a list")

        normalized: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @field_validator("scheduled_end")
    @classmethod
    def validate_schedule(cls, value: datetime, info: Any) -> datetime:
        start = info.data.get("scheduled_start")
        if start and value <= start:
            raise ValueError("scheduled_end must be after scheduled_start")
        return value


class MeetingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: Optional[str]
    meeting_type: Optional[str]
    platform: str
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: Optional[datetime]
    actual_end: Optional[datetime]
    status: str
    summary: Optional[str]
    created_by: Optional[UUID] = None
    attendee_ids: Optional[List[str]] = None
    attendee_count: Optional[int] = 0
    agenda: Optional[Union[List[str], Dict[str, Any]]]
    tags: Optional[List[str]] = None
    created_at: datetime


class InlineActionItemResponse(BaseModel):
    """Action item included inline in meeting detail"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: Optional[str] = None
    assigned_to_user_id: Optional[UUID] = None
    status: str = "open"
    priority: str = "medium"
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    extraction_method: Optional[str] = None
    confidence_score: Optional[float] = None
    created_at: datetime


class InlineMentionResponse(BaseModel):
    """Mention included inline in meeting detail"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meeting_id: UUID
    user_id: Optional[UUID] = None
    mention_type: str
    mentioned_text: str
    relevance_score: Optional[float] = None
    urgency_score: Optional[float] = None
    sentiment: Optional[str] = None
    notification_read: bool = False
    created_at: datetime


class MeetingDetail(MeetingResponse):
    """Detailed meeting response with relationships"""
    transcription_status: str
    analysis_status: str
    key_decisions: Optional[List[Dict[str, Any]]]
    discussion_topics: Optional[List[str]]
    sentiment_score: Optional[float]
    meeting_quality_score: Optional[float]
    action_items: List[InlineActionItemResponse] = []
    mentions: List[InlineMentionResponse] = []


class PreBriefMeetingContext(BaseModel):
    title: str
    agenda: str
    attendees: List[str]


class PreBriefTask(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    status: str
    priority: Optional[str] = None
    due_date: Optional[str] = None
    meeting_id: Optional[str] = None


class PreBriefMention(BaseModel):
    id: str
    meeting_id: str
    text: str
    type: str
    confidence: Optional[float] = None
    created_at: Optional[str] = None


class PreBriefPreparation(BaseModel):
    pending_tasks: List[PreBriefTask]
    relevant_mentions: List[PreBriefMention]
    expected_questions: List[str]


class PreBriefDevelopment(BaseModel):
    type: str
    title: str
    summary: str
    scheduled_start: Optional[str] = None


class PreMeetingBriefResponse(BaseModel):
    meeting_context: PreBriefMeetingContext
    user_preparation: PreBriefPreparation
    recent_developments: List[PreBriefDevelopment]
    suggested_points: List[str]
    importance: str


@router.post("/", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    meeting_data: MeetingCreate,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Create new meeting"""
    attendee_ids = _resolve_attendee_ids(db, meeting_data.attendee_ids)

    meeting = Meeting(
        title=meeting_data.title,
        description=meeting_data.description,
        meeting_type=meeting_data.meeting_type,
        platform=meeting_data.platform,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        scheduled_start=meeting_data.scheduled_start,
        scheduled_end=meeting_data.scheduled_end,
        organizer_id=current_user.id,
        attendee_ids=attendee_ids,
        attendee_count=len(attendee_ids),
        agenda=meeting_data.agenda,
        tags=meeting_data.tags,
        status="scheduled",
    )
    
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    
    return meeting


@router.get("/", response_model=List[MeetingResponse])
async def list_meetings(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """List user meetings"""
    query = select(Meeting).where(
        Meeting.organization_id == current_user.organization_id,
        Meeting.deleted_at.is_(None),
    )

    if not _is_admin(current_user):
        attendee_filters = [
            cast(Meeting.attendee_ids, String).contains(token)
            for token in _user_attendee_tokens(current_user)
        ]
        query = query.where(
            (Meeting.organizer_id == current_user.id) |
            (Meeting.created_by == current_user.id) |
            or_(*attendee_filters)
        )
    
    if status:
        query = query.where(Meeting.status == status)
    
    query = query.order_by(desc(Meeting.scheduled_start)).offset(skip).limit(limit)
    
    result = db.execute(query)
    meetings = result.scalars().all()
    
    return meetings


@router.get("/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get meeting details with inline action items and mentions"""
    result = db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )
    
    # Check access - user must be organizer or attendee
    attendee_tokens = set(_user_attendee_tokens(current_user))
    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Fetch all action items for this meeting (not user-scoped)
    action_items_result = db.execute(
        select(ActionItem)
        .where(
            ActionItem.meeting_id == meeting_id,
            ActionItem.organization_id == meeting.organization_id,
        )
        .order_by(ActionItem.created_at.desc())
    )
    meeting_action_items = action_items_result.scalars().all()

    # Fetch all mentions for this meeting (not user-scoped)
    mentions_result = db.execute(
        select(Mention)
        .where(
            Mention.meeting_id == meeting_id,
            Mention.organization_id == meeting.organization_id,
        )
        .order_by(Mention.created_at.desc())
    )
    meeting_mentions = mentions_result.scalars().all()

    # Build response with inline data
    meeting_dict = {
        col.name: getattr(meeting, col.name)
        for col in Meeting.__table__.columns
    }
    meeting_dict["action_items"] = meeting_action_items
    meeting_dict["mentions"] = meeting_mentions

    return MeetingDetail.model_validate(meeting_dict)


@router.post("/{meeting_id}/upload", response_model=MeetingResponse)
async def upload_recording(
    meeting_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload meeting recording for processing"""
    result = db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )

    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Save file
    import os
    os.makedirs("uploads/recordings", exist_ok=True)
    file_path = f"uploads/recordings/{meeting_id}_{file.filename}"
    
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # Update meeting
    meeting.recording_path = file_path  # type: ignore
    meeting.status = "processing"  # type: ignore
    meeting.transcription_status = "processing"  # type: ignore
    
    db.commit()
    
    # Trigger background processing
    background_tasks.add_task(process_meeting_recording_background, meeting.id, file_path)
    
    return meeting


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Delete meeting (soft delete)"""
    result = db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )
    
    if str(getattr(meeting, "organization_id", "") or "") != str(getattr(current_user, "organization_id", "") or ""):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not _is_admin(current_user) and meeting.organizer_id != current_user.id:  # type: ignore
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organizer can delete meeting",
        )
    
    meeting.deleted_at = datetime.utcnow()  # type: ignore
    db.commit()
    
    return None


@router.get("/{meeting_id}/catchup", response_model=Dict[str, Any])
async def get_meeting_catchup(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Generate a catch-up package for the current user for a meeting they missed."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    catchup = await absence_management_service.generate_catchup_for_user(db, meeting, current_user)
    return catchup


@router.get("/{meeting_id}/pre-brief", response_model=PreMeetingBriefResponse)
async def get_pre_meeting_brief(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return a personalized pre-meeting intelligence brief for the current user."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return await pre_meeting_brief_service.generate_api_brief_for_user(db, meeting, current_user)


@router.post("/{meeting_id}/catchup/send", response_model=Dict[str, Any])
async def send_meeting_catchup(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Send a catch-up Slack DM to the current user for a meeting they missed."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    success = await absence_management_service.send_catchup_to_user(db, meeting, current_user)
    return {"sent": success, "meeting_id": str(meeting_id), "user_id": str(getattr(current_user, "id", ""))}


@router.get("/{meeting_id}/absentees", response_model=List[Dict[str, Any]])
async def get_meeting_absentees(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get list of users who were invited but did not attend the meeting."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if str(getattr(meeting, "organization_id", "") or "") != str(getattr(current_user, "organization_id", "") or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not _is_admin(current_user) and getattr(meeting, "organizer_id", None) != getattr(current_user, "id", None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the organizer can view absentees")

    absentees = absence_management_service.find_absentees_for_meeting(db, meeting)
    return [
        {
            "user_id": str(getattr(u, "id", "")),
            "full_name": str(getattr(u, "full_name", "") or ""),
            "email": str(getattr(u, "email", "") or ""),
        }
        for u in absentees
    ]

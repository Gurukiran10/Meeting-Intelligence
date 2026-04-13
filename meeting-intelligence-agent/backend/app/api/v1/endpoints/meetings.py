"""
Meetings API Endpoints
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, cast, String
from pydantic import BaseModel

from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user
from app.tasks.meeting_processor import process_meeting_recording_background
from app.services.absence_management import absence_management_service

router = APIRouter()


class MeetingCreate(BaseModel):
    title: str
    description: Optional[str] = None
    meeting_type: Optional[str] = None
    platform: str = "manual"
    scheduled_start: datetime
    scheduled_end: datetime
    attendee_ids: List[str] = []
    agenda: Optional[dict] = None
    tags: List[str] = []


class MeetingResponse(BaseModel):
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
    created_at: datetime
    
    class Config:
        from_attributes = True


class MeetingDetail(MeetingResponse):
    """Detailed meeting response with relationships"""
    transcription_status: str
    analysis_status: str
    key_decisions: Optional[List[Dict[str, Any]]]
    discussion_topics: Optional[List[str]]
    sentiment_score: Optional[float]
    meeting_quality_score: Optional[float]


@router.post("/", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    meeting_data: MeetingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create new meeting"""
    meeting = Meeting(
        title=meeting_data.title,
        description=meeting_data.description,
        meeting_type=meeting_data.meeting_type,
        platform=meeting_data.platform,
        scheduled_start=meeting_data.scheduled_start,
        scheduled_end=meeting_data.scheduled_end,
        organizer_id=current_user.id,
        attendee_ids=meeting_data.attendee_ids,
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List user meetings"""
    query = select(Meeting).where(
        (Meeting.organizer_id == current_user.id) |
        (cast(Meeting.attendee_ids, String).contains(str(current_user.id)))
    ).where(Meeting.deleted_at.is_(None))
    
    if status:
        query = query.where(Meeting.status == status)
    
    query = query.order_by(desc(Meeting.scheduled_start)).offset(skip).limit(limit)
    
    result = db.execute(query)
    meetings = result.scalars().all()
    
    return meetings


@router.get("/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get meeting details"""
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
    if (
        meeting.organizer_id != current_user.id
        and str(current_user.id) not in (meeting.attendee_ids or [])
    ):  # type: ignore
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    return meeting


@router.post("/{meeting_id}/upload", response_model=MeetingResponse)
async def upload_recording(
    meeting_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
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
    
    # Save file
    import os
    os.makedirs("uploads/recordings", exist_ok=True)
    file_path = f"uploads/recordings/{meeting_id}_{file.filename}"
    
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # Update meeting
    meeting.recording_path = file_path  # type: ignore
    meeting.status = "transcribing"  # type: ignore
    meeting.transcription_status = "processing"  # type: ignore
    
    db.commit()
    
    # Trigger background processing
    background_tasks.add_task(process_meeting_recording_background, meeting.id, file_path)
    
    return meeting


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
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
    
    if meeting.organizer_id != current_user.id:  # type: ignore
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a catch-up package for the current user for a meeting they missed."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    catchup = await absence_management_service.generate_catchup_for_user(db, meeting, current_user)
    return catchup


@router.post("/{meeting_id}/catchup/send", response_model=Dict[str, Any])
async def send_meeting_catchup(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a catch-up Slack DM to the current user for a meeting they missed."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    success = await absence_management_service.send_catchup_to_user(db, meeting, current_user)
    return {"sent": success, "meeting_id": str(meeting_id), "user_id": str(getattr(current_user, "id", ""))}


@router.get("/{meeting_id}/absentees", response_model=List[Dict[str, Any]])
async def get_meeting_absentees(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get list of users who were invited but did not attend the meeting."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if getattr(meeting, "organizer_id", None) != getattr(current_user, "id", None):
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

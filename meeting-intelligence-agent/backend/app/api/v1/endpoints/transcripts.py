"""
Transcripts API Endpoints
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.transcript import Transcript
from app.models.user import User

router = APIRouter()


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meeting_id: UUID
    segment_number: int
    speaker_id: Optional[str] = None
    text: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None
    confidence: Optional[float] = None


@router.get("/", response_model=List[TranscriptSegment])
async def list_transcripts(
    meeting_id: Optional[UUID] = None,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return transcript segments, optionally filtered by meeting_id."""
    query = select(Transcript)

    if meeting_id:
        # Verify the user can access this meeting
        meeting_result = db.execute(
            select(Meeting).where(
                Meeting.id == meeting_id,
                Meeting.organization_id == current_user.organization_id,
                Meeting.deleted_at.is_(None),
            )
        )
        meeting = meeting_result.scalar_one_or_none()
        if not meeting:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
        query = query.where(Transcript.meeting_id == meeting_id)
    else:
        # Scope to org by joining through meetings
        query = query.join(Meeting, Transcript.meeting_id == Meeting.id).where(
            Meeting.organization_id == current_user.organization_id,
            Meeting.deleted_at.is_(None),
        )

    query = query.order_by(Transcript.meeting_id, Transcript.segment_number)
    result = db.execute(query)
    return result.scalars().all()

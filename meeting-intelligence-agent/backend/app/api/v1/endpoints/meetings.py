"""
Meetings API Endpoints
"""
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from uuid import UUID, uuid4
import mimetypes
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
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
from app.services.meeting_importance import score_meeting_for_user
from app.services.attendee_optimization import analyze_participation
from app.services.collaborative_prep import get_prep_summary

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
        if normalized not in {"zoom", "manual", "google_meet"}:
            raise ValueError("Platform must be 'zoom', 'google_meet', or 'manual'")
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


class ImportanceScore(BaseModel):
    label: str
    score: int
    emoji: str
    recommendation: str
    reasons: List[str] = []
    warnings: List[str] = []


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
    importance: Optional[ImportanceScore] = None
    meeting_url: Optional[str] = None
    calendar_url: Optional[str] = None


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
    has_recording: bool = False


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


async def _get_google_access_token_for_user(db: Session, user: User) -> Optional[str]:
    """Return a valid Google access token for the user, refreshing if expired."""
    from app.api.v1.endpoints.integrations import _get_google_access_token, _get_integration
    try:
        return await _get_google_access_token(db, user)
    except Exception:
        return None


async def _create_google_calendar_event(
    access_token: str,
    title: str,
    description: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
    attendee_emails: List[str],
    calendar_id: str = "primary",
) -> Dict[str, str]:
    """Create a Google Calendar event with a Meet conference link.

    Returns dict with keys: event_id, meet_url, calendar_url.
    """
    body = {
        "summary": title,
        "description": description or "",
        "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "UTC"},
        "attendees": [{"email": e} for e in attendee_emails if e],
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"conferenceDataVersion": "1"},
            json=body,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Google Calendar API error {resp.status_code}: {resp.text[:300]}",
        )
    data = resp.json()
    return {
        "event_id": data.get("id", ""),
        "meet_url": data.get("hangoutLink", ""),
        "calendar_url": data.get("htmlLink", ""),
    }


async def _delete_google_calendar_event(
    access_token: str,
    event_id: str,
    calendar_id: str = "primary",
) -> None:
    """Delete a Google Calendar event by event_id. Silently ignores 404 (already deleted)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code not in (200, 204, 404):
        logger.warning("Google Calendar delete returned %s: %s", resp.status_code, resp.text[:200])


@router.post("/", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    meeting_data: MeetingCreate,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Create new meeting. If platform is google_meet and user has Google connected,
    creates a real Google Calendar event with a Meet conference link."""
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

    # If google_meet platform and user is connected (via auth OR integrations), create a real calendar event
    from app.api.v1.endpoints.integrations import _get_integration as _gi
    _google_int = _gi(current_user, "google")
    _google_is_connected = current_user.google_connected or bool(_google_int and (_google_int.get("access_token") or _google_int.get("refresh_token")))
    if meeting_data.platform == "google_meet" and _google_is_connected:
        access_token = await _get_google_access_token_for_user(db, current_user)
        if access_token:
            try:
                # Collect attendee emails for calendar invites
                attendee_emails: List[str] = []
                for uid in attendee_ids:
                    u = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
                    if u and u.email:
                        attendee_emails.append(u.email)

                from app.api.v1.endpoints.integrations import _get_integration
                google_config = _get_integration(current_user, "google")
                calendar_id = google_config.get("calendar_id", "primary") or "primary"

                cal_event = await _create_google_calendar_event(
                    access_token=access_token,
                    title=meeting_data.title,
                    description=meeting_data.description,
                    start_dt=meeting_data.scheduled_start,
                    end_dt=meeting_data.scheduled_end,
                    attendee_emails=attendee_emails,
                    calendar_id=calendar_id,
                )
                if cal_event["meet_url"]:
                    meeting.meeting_url = cal_event["meet_url"]        # type: ignore[attr-defined]
                if cal_event["event_id"]:
                    meeting.external_id = cal_event["event_id"]        # type: ignore[attr-defined]
                meeting.meeting_metadata = {                            # type: ignore[attr-defined]
                    "calendar_url": cal_event["calendar_url"],
                    "calendar_event_id": cal_event["event_id"],
                }
            except Exception as exc:
                # Non-fatal: save meeting without calendar event
                import logging
                logging.getLogger(__name__).warning(
                    "Google Calendar event creation failed (meeting saved without link): %s", exc
                )

    # If zoom platform and user has Zoom connected, create a real Zoom meeting
    if meeting_data.platform == "zoom":
        try:
            from app.api.v1.endpoints.integrations import _get_integration, _get_zoom_access_token
            zoom_config = _get_integration(current_user, "zoom")
            if zoom_config and zoom_config.get("account_id"):
                zoom_token = await _get_zoom_access_token(db, current_user)
                start_iso = meeting_data.scheduled_start.strftime("%Y-%m-%dT%H:%M:%SZ")
                duration_mins = max(1, int((meeting_data.scheduled_end - meeting_data.scheduled_start).total_seconds() / 60))
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        "https://api.zoom.us/v2/users/me/meetings",
                        headers={"Authorization": f"Bearer {zoom_token}", "Content-Type": "application/json"},
                        json={
                            "topic": meeting_data.title,
                            "type": 2,
                            "start_time": start_iso,
                            "duration": duration_mins,
                            "agenda": meeting_data.description or "",
                            "settings": {"join_before_host": True, "waiting_room": False},
                        },
                    )
                if resp.status_code in (200, 201):
                    zm = resp.json()
                    meeting.meeting_url = zm.get("join_url", "")          # type: ignore[attr-defined]
                    meeting.external_id = str(zm.get("id", ""))           # type: ignore[attr-defined]
                    meeting.meeting_metadata = {"zoom_start_url": zm.get("start_url", "")}  # type: ignore[attr-defined]
                else:
                    import logging
                    logging.getLogger(__name__).warning("Zoom meeting creation failed %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Zoom meeting creation error (saved without link): %s", exc)

    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    # Attach calendar_url from metadata for the response
    response = MeetingResponse.model_validate(meeting)
    if meeting.meeting_metadata:
        response.calendar_url = meeting.meeting_metadata.get("calendar_url")
    if meeting.meeting_url:
        response.meeting_url = meeting.meeting_url                        # type: ignore[attr-defined]
    return response


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
    
    query = query.order_by(desc(Meeting.created_at)).offset(skip).limit(limit)
    
    result = db.execute(query)
    meetings = result.scalars().all()

    # Attach importance scores (use cached value from meeting_metadata when available)
    enriched = []
    for m in meetings:
        m_dict = {col.name: getattr(m, col.name) for col in Meeting.__table__.columns}
        meta = dict(m.meeting_metadata or {})
        cached = meta.get("importance", {}).get(str(current_user.id))
        if not cached:
            try:
                cached = score_meeting_for_user(db, m, current_user)
                meta.setdefault("importance", {})[str(current_user.id)] = cached
                m.meeting_metadata = meta
                db.commit()
            except Exception:
                cached = None
        m_dict["importance"] = cached
        enriched.append(m_dict)

    return [MeetingResponse.model_validate(m) for m in enriched]


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
    recording_path = meeting_dict.get("recording_path") or ""
    meeting_dict["has_recording"] = bool(recording_path and os.path.exists(recording_path))

    # Attach importance score (cache in meeting_metadata)
    meta = dict(meeting.meeting_metadata or {})
    cached_importance = meta.get("importance", {}).get(str(current_user.id))
    if not cached_importance:
        try:
            cached_importance = score_meeting_for_user(db, meeting, current_user)
            meta.setdefault("importance", {})[str(current_user.id)] = cached_importance
            meeting.meeting_metadata = meta
            db.commit()
        except Exception:
            cached_importance = None
    meeting_dict["importance"] = cached_importance

    return MeetingDetail.model_validate(meeting_dict)


@router.get("/{meeting_id}/recording")
async def stream_recording(
    meeting_id: UUID,
    request: Request,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Stream the meeting recording audio file with HTTP range support."""
    result = db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    path = (meeting.recording_path or "").strip()
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")

    file_size = os.path.getsize(path)
    mime_type = mimetypes.guess_type(path)[0] or "audio/webm"

    range_header = request.headers.get("range")
    if range_header:
        # Parse "bytes=start-end"
        range_match = range_header.strip().replace("bytes=", "").split("-")
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else file_size - 1
        end = min(end, file_size - 1)
        chunk_size = end - start + 1

        def _iter():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            _iter(),
            status_code=206,
            media_type=mime_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    def _iter_full():
        with open(path, "rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                yield data

    return StreamingResponse(
        _iter_full(),
        media_type=mime_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


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
    
    # Delete the Google Calendar event if one was created
    event_id = getattr(meeting, "external_id", None)
    platform = getattr(meeting, "platform", None)
    if event_id and platform == "google_meet":
        try:
            access_token = await _get_google_access_token_for_user(db, current_user)
            if access_token:
                from app.api.v1.endpoints.integrations import _get_integration
                google_config = _get_integration(current_user, "google")
                calendar_id = google_config.get("calendar_id", "primary") or "primary"
                await _delete_google_calendar_event(access_token, event_id, calendar_id)
        except Exception as exc:
            logger.warning("Could not delete Google Calendar event %s: %s", event_id, exc)

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


@router.get("/{meeting_id}/importance", response_model=Dict[str, Any])
async def get_meeting_importance(
    meeting_id: UUID,
    refresh: bool = False,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return the importance / skip-recommendation score for this meeting for the current user."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    meta = dict(meeting.meeting_metadata or {})
    cached = meta.get("importance", {}).get(str(current_user.id))

    if not cached or refresh:
        cached = score_meeting_for_user(db, meeting, current_user)
        meta.setdefault("importance", {})[str(current_user.id)] = cached
        meeting.meeting_metadata = meta
        db.commit()

    return cached


@router.get("/{meeting_id}/participation", response_model=Dict[str, Any])
async def get_meeting_participation(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return attendee participation breakdown — who spoke, who was silent, effectiveness score."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return analyze_participation(db, meeting)


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


@router.get("/{meeting_id}/prep", response_model=Dict[str, Any])
async def get_meeting_prep(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Collaborative prep: agenda suggestions + attendee optimization for an upcoming meeting."""
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return get_prep_summary(db, str(meeting_id))


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


@router.get("/{meeting_id}/bot-status")
async def get_bot_status(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Return the live bot session state for a meeting.
    Combines Redis live state (if available) with the DB meeting status
    so the frontend always gets a consistent answer.

    Status values:
      pending           — scheduled, bot not yet triggered
      launching         — Chromium starting up
      navigating        — loading meet URL
      pre_join          — on pre-join screen
      joining           — clicking join button
      waiting_admission — in waiting room
      in_meeting        — confirmed inside
      waiting_for_host  — joined but alone, retry scheduled
      recording         — actively recording
      stopping          — stopping recording
      completed         — recording saved, processing queued
      failed            — all retries exhausted
      host_absent       — max retries reached, host never showed
      bot_rejected      — host denied admission
      cancelled         — manually stopped
    """
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    ).scalar_one_or_none()

    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    from app.services.bot_state import get_full_state, BotStatus

    full_state = await get_full_state(str(meeting_id))

    # Fall back to in-memory state for bots still running in FastAPI process
    if full_state["status"] == "unknown":
        from app.services.meet_bot import get_bot_status as _get_mem_status
        mem = _get_mem_status(str(meeting.organizer_id)) or {}
        if mem.get("status"):
            full_state["status"]  = mem["status"]
            full_state["message"] = BotStatus.message(mem["status"])

    return {
        "meeting_id":       str(meeting_id),
        "db_status":        meeting.status,
        # Canonical fields — always present
        "bot_status":       full_state["status"],
        "message":          full_state["message"],
        "attempt":          full_state["attempt"],
        "max_attempts":     full_state["max_attempts"],
        "next_retry_at":    full_state["next_retry_at"],
        "rejection_reason": full_state["rejection_reason"],
        "last_error":       full_state["last_error"],
        "meet_url":         full_state.get("meet_url") or str(meeting.meeting_url or ""),
        "platform":         full_state.get("platform") or str(meeting.platform or ""),
        "session_elapsed_s": full_state["session_elapsed_s"],
        "created_at":       full_state["created_at"],
        "updated_at":       full_state["updated_at"],
    }


@router.post("/{meeting_id}/trigger-bot")
async def trigger_bot(
    meeting_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Manually trigger the bot for a specific meeting.
    Uses the Celery task when available, falls back to in-process asyncio.
    Bot dedup is enforced via Redis lock — safe to call multiple times.
    """
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    ).scalar_one_or_none()

    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if not _can_access_meeting(current_user, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not meeting.meeting_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meeting has no URL")

    if meeting.status in ("completed", "transcribing", "analyzing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Meeting already {meeting.status}",
        )

    from app.core.config import settings
    from app.services.bot_state import acquire_lock, get_bot_state, BotStatus

    mid_str  = str(meeting_id)
    url      = str(meeting.meeting_url)
    platform = str(meeting.platform or "google_meet")
    uid      = str(meeting.organizer_id)
    org_id   = str(meeting.organization_id)

    # Check if bot is already running
    state = await get_bot_state(mid_str)
    active_statuses = {
        BotStatus.LAUNCHING, BotStatus.NAVIGATING, BotStatus.PRE_JOIN,
        BotStatus.JOINING, BotStatus.WAITING_ADMISSION, BotStatus.IN_MEETING,
        BotStatus.RECORDING, BotStatus.STOPPING,
    }
    if state and state.get("status") in active_statuses:
        return {
            "queued":  False,
            "message": f"Bot already active (status={state['status']})",
            "status":  state["status"],
        }

    # Try Celery first
    try:
        from app.tasks.auto_join import run_bot_session_task
        run_bot_session_task.apply_async(
            kwargs={
                "meeting_id":          mid_str,
                "meet_url":            url,
                "user_id":             uid,
                "organization_id":     org_id,
                "platform":            platform,
                "bot_display_name":    settings.MEET_BOT_DISPLAY_NAME,
                "stay_duration_seconds": settings.MEET_BOT_STAY_DURATION_SECONDS,
                "recordings_dir":      settings.RECORDINGS_DIR,
                "attempt":             1,
            },
            queue="bots",
        )
        return {"queued": True, "method": "celery", "meeting_id": mid_str}

    except Exception as celery_err:
        import logging
        logging.getLogger(__name__).warning(
            "Celery unavailable, falling back to in-process bot: %s", celery_err
        )

    # Fallback: in-process asyncio (existing behaviour)
    if platform == "zoom":
        from app.services.zoom_bot import join_zoom_meeting
        import asyncio
        asyncio.create_task(join_zoom_meeting(
            zoom_url=url, user_id=uid, organization_id=org_id,
            meeting_id=mid_str,
            bot_display_name=settings.MEET_BOT_DISPLAY_NAME,
            stay_duration_seconds=settings.MEET_BOT_STAY_DURATION_SECONDS,
            recordings_dir=settings.RECORDINGS_DIR,
        ))
    else:
        from app.services.meet_bot import join_google_meet
        import asyncio
        asyncio.create_task(join_google_meet(
            meet_url=url, user_id=uid, organization_id=org_id,
            meeting_id=mid_str,
            bot_display_name=settings.MEET_BOT_DISPLAY_NAME,
            stay_duration_seconds=settings.MEET_BOT_STAY_DURATION_SECONDS,
            recordings_dir=settings.RECORDINGS_DIR,
        ))

    return {"queued": True, "method": "in_process", "meeting_id": mid_str}

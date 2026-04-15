"""Personal and organization dashboards"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_admin, require_org_member
from app.core.database import get_db
from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.mention import Mention
from app.models.user import User
from app.services.pre_meeting_briefs import pre_meeting_brief_service

router = APIRouter()


def _serialize_meeting(meeting: Meeting) -> dict:
    return {
        "id": str(meeting.id),
        "title": meeting.title,
        "description": meeting.description,
        "platform": meeting.platform,
        "status": meeting.status,
        "scheduled_start": meeting.scheduled_start.isoformat() if isinstance(meeting.scheduled_start, datetime) else meeting.scheduled_start,
        "scheduled_end": meeting.scheduled_end.isoformat() if isinstance(meeting.scheduled_end, datetime) else meeting.scheduled_end,
        "attendee_count": meeting.attendee_count,
    }


def _serialize_action_item(item: ActionItem) -> dict:
    return {
        "id": str(item.id),
        "meeting_id": str(item.meeting_id),
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "priority": item.priority,
        "due_date": item.due_date.isoformat() if isinstance(item.due_date, datetime) else item.due_date,
        "assigned_to_user_id": str(item.assigned_to_user_id) if item.assigned_to_user_id else None,
        "confidence_score": item.confidence_score,
    }


def _serialize_mention(mention: Mention) -> dict:
    return {
        "id": str(mention.id),
        "meeting_id": str(mention.meeting_id),
        "user_id": str(mention.user_id),
        "mention_type": mention.mention_type,
        "mentioned_text": mention.mentioned_text,
        "relevance_score": mention.relevance_score,
        "urgency_score": mention.urgency_score,
        "notification_read": mention.notification_read,
        "created_at": mention.created_at.isoformat() if isinstance(mention.created_at, datetime) else mention.created_at,
    }


@router.get("/me/dashboard", response_model=dict)
async def my_dashboard(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    meetings = db.execute(
        select(Meeting).where(
            Meeting.organization_id == current_user.organization_id,
            Meeting.deleted_at.is_(None),
        )
    ).scalars().all()
    my_meetings = [
        meeting for meeting in meetings
        if meeting.organizer_id == current_user.id
        or meeting.created_by == current_user.id
        or current_user.email in (meeting.attendee_ids or [])
        or current_user.username in (meeting.attendee_ids or [])
        or str(current_user.id) in [str(item) for item in (meeting.attendee_ids or [])]
    ]
    my_tasks = db.execute(
        select(ActionItem).where(
            ActionItem.organization_id == current_user.organization_id,
            ActionItem.assigned_to_user_id == current_user.id,
        )
    ).scalars().all()
    my_mentions = db.execute(
        select(Mention).where(
            Mention.organization_id == current_user.organization_id,
            Mention.user_id == current_user.id,
        )
    ).scalars().all()
    return {
        "my_meetings": [_serialize_meeting(meeting) for meeting in my_meetings],
        "my_tasks": [_serialize_action_item(item) for item in my_tasks],
        "my_mentions": [_serialize_mention(mention) for mention in my_mentions],
    }


@router.get("/org/dashboard", response_model=dict)
async def org_dashboard(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.execute(select(User).where(User.organization_id == current_user.organization_id)).scalars().all()
    meetings = db.execute(
        select(Meeting).where(
            Meeting.organization_id == current_user.organization_id,
            Meeting.deleted_at.is_(None),
        )
    ).scalars().all()
    tasks = db.execute(select(ActionItem).where(ActionItem.organization_id == current_user.organization_id)).scalars().all()
    mentions = db.execute(select(Mention).where(Mention.organization_id == current_user.organization_id)).scalars().all()
    return {
        "organization_id": str(current_user.organization_id),
        "users_count": len(users),
        "meetings_count": len(meetings),
        "open_tasks_count": len([task for task in tasks if task.status != "completed"]),
        "mentions_count": len(mentions),
        "recent_meetings": [_serialize_meeting(meeting) for meeting in meetings[:5]],
        "team_tasks": [_serialize_action_item(item) for item in tasks[:10]],
    }


@router.get("/me/pre-briefs", response_model=list[dict])
async def my_pre_briefs(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    return await pre_meeting_brief_service.list_upcoming_briefs_for_user(db, current_user)

"""Analytics API Endpoints"""
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, cast, String
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member, require_admin
from app.core.database import get_db
from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.user import User
from app.services.meeting_analytics import meeting_analytics_service

router = APIRouter()


class MeetingStats(BaseModel):
    total_meetings: int
    total_hours: float
    avg_duration_minutes: float
    this_week_count: int
    this_month_count: int


class ActionItemStats(BaseModel):
    total: int
    completed: int
    in_progress: int
    overdue: int
    completion_rate: float


class AnalyticsDashboard(BaseModel):
    meeting_stats: MeetingStats
    action_item_stats: ActionItemStats
    time_saved_hours: float
    decision_velocity: float


class MeetingInsight(BaseModel):
    id: str
    title: str
    status: str
    scheduled_start: datetime
    duration_minutes: float
    action_items_count: int
    decisions_count: int
    sentiment_score: float


class SentimentPoint(BaseModel):
    label: str
    score: float


class MeetingEfficiencyResponse(BaseModel):
    avg_decisions_per_hour: float
    avg_action_items_per_hour: float
    completion_ratio: float
    status_breakdown: dict
    sentiment_trend: List[SentimentPoint]
    recent_meetings: List[MeetingInsight]


@router.get("/dashboard", response_model=AnalyticsDashboard)
async def get_analytics_dashboard(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get analytics dashboard data from real database values"""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    meeting_query = select(Meeting).where(
        Meeting.organization_id == current_user.organization_id,
        Meeting.deleted_at.is_(None),
    )
    if current_user.role != "admin":
        meeting_query = meeting_query.where(
            or_(
                Meeting.organizer_id == current_user.id,
                Meeting.created_by == current_user.id,
                cast(Meeting.attendee_ids, String).contains(str(current_user.id)),
                cast(Meeting.attendee_ids, String).contains(current_user.email),
                cast(Meeting.attendee_ids, String).contains(current_user.username),
            )
        )
    meetings = db.execute(meeting_query).scalars().all()

    total_meetings = len(meetings)
    total_minutes = 0.0
    this_week_count = 0
    this_month_count = 0
    total_decisions = 0

    for meeting in meetings:
        if meeting.scheduled_start and meeting.scheduled_start >= week_ago:
            this_week_count += 1
        if meeting.scheduled_start and meeting.scheduled_start >= month_ago:
            this_month_count += 1

        if meeting.actual_start and meeting.actual_end:
            total_minutes += max(0.0, (meeting.actual_end - meeting.actual_start).total_seconds() / 60)
        elif meeting.scheduled_start and meeting.scheduled_end:
            total_minutes += max(0.0, (meeting.scheduled_end - meeting.scheduled_start).total_seconds() / 60)

        if isinstance(meeting.key_decisions, list):
            total_decisions += len(meeting.key_decisions)

    total_hours = total_minutes / 60 if total_minutes else 0.0
    avg_duration_minutes = (total_minutes / total_meetings) if total_meetings else 0.0
    decision_velocity = (total_decisions / total_hours) if total_hours > 0 else 0.0

    action_query = select(ActionItem).where(
        ActionItem.organization_id == current_user.organization_id
    )
    if current_user.role != "admin":
        action_query = action_query.where(ActionItem.assigned_to_user_id == current_user.id)
    action_items = db.execute(action_query).scalars().all()

    total_actions = len(action_items)
    completed_actions = sum(1 for item in action_items if item.status == "completed")
    in_progress_actions = sum(1 for item in action_items if item.status == "in_progress")
    overdue_actions = sum(
        1
        for item in action_items
        if item.due_date and item.due_date < now and item.status != "completed"
    )
    completion_rate = (completed_actions / total_actions * 100) if total_actions else 0.0

    time_saved_hours = round(total_meetings * 0.5, 1)

    return AnalyticsDashboard(
        meeting_stats=MeetingStats(
            total_meetings=total_meetings,
            total_hours=round(total_hours, 1),
            avg_duration_minutes=round(avg_duration_minutes, 1),
            this_week_count=this_week_count,
            this_month_count=this_month_count,
        ),
        action_item_stats=ActionItemStats(
            total=total_actions,
            completed=completed_actions,
            in_progress=in_progress_actions,
            overdue=overdue_actions,
            completion_rate=round(completion_rate, 1),
        ),
        time_saved_hours=time_saved_hours,
        decision_velocity=round(decision_velocity, 2),
    )


@router.get("/meeting-efficiency")
async def get_meeting_efficiency(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get real meeting efficiency metrics"""
    meetings = db.execute(
        select(Meeting).where(
            Meeting.organization_id == current_user.organization_id,
            Meeting.deleted_at.is_(None),
        )
    ).scalars().all()
    if current_user.role != "admin":
        meetings = [
            meeting for meeting in meetings
            if meeting.organizer_id == current_user.id
            or meeting.created_by == current_user.id
            or str(current_user.id) in str(meeting.attendee_ids or [])
            or current_user.email in str(meeting.attendee_ids or [])
            or current_user.username in str(meeting.attendee_ids or [])
        ]

    action_items = db.execute(
        select(ActionItem).where(ActionItem.organization_id == current_user.organization_id)
    ).scalars().all()
    if current_user.role != "admin":
        action_items = [item for item in action_items if item.assigned_to_user_id == current_user.id]

    total_minutes = 0.0
    total_decisions = 0
    status_counter: Counter = Counter()
    recent_meetings: List[MeetingInsight] = []
    sentiment_points: List[SentimentPoint] = []

    sorted_meetings = sorted(
        meetings,
        key=lambda meeting: meeting.scheduled_start or meeting.created_at,
        reverse=True,
    )

    for meeting in sorted_meetings:
        duration_minutes = 0.0
        if meeting.actual_start and meeting.actual_end:
            duration_minutes = max(0.0, (meeting.actual_end - meeting.actual_start).total_seconds() / 60)
        elif meeting.scheduled_start and meeting.scheduled_end:
            duration_minutes = max(0.0, (meeting.scheduled_end - meeting.scheduled_start).total_seconds() / 60)

        total_minutes += duration_minutes
        decisions_count = len(meeting.key_decisions) if isinstance(meeting.key_decisions, list) else 0
        total_decisions += decisions_count
        status_counter[str(meeting.status or "unknown").lower()] += 1

        if len(recent_meetings) < 5:
            recent_meetings.append(
                MeetingInsight(
                    id=str(meeting.id),
                    title=meeting.title,
                    status=meeting.status or "unknown",
                    scheduled_start=meeting.scheduled_start,
                    duration_minutes=round(duration_minutes, 1),
                    action_items_count=len(getattr(meeting, "action_items", []) or []),
                    decisions_count=decisions_count,
                    sentiment_score=round(meeting.sentiment_score or 0.0, 2),
                )
            )

    for meeting in reversed(sorted_meetings[:6]):
        date_value = meeting.scheduled_start or meeting.created_at
        sentiment_points.append(
            SentimentPoint(
                label=date_value.strftime("%b %d"),
                score=round(meeting.sentiment_score or 0.0, 2),
            )
        )

    total_hours = total_minutes / 60 if total_minutes else 0.0
    avg_decisions_per_hour = round(total_decisions / total_hours, 2) if total_hours else 0.0
    avg_action_items_per_hour = round(len(action_items) / total_hours, 2) if total_hours else 0.0
    completed_actions = sum(1 for item in action_items if item.status == "completed")
    completion_ratio = round((completed_actions / len(action_items)) * 100, 1) if action_items else 0.0

    return MeetingEfficiencyResponse(
        avg_decisions_per_hour=avg_decisions_per_hour,
        avg_action_items_per_hour=avg_action_items_per_hour,
        completion_ratio=completion_ratio,
        status_breakdown=dict(status_counter),
        sentiment_trend=sentiment_points,
        recent_meetings=recent_meetings,
    )


@router.get("/intelligence-report", response_model=Dict[str, Any])
async def get_intelligence_report(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return full meeting intelligence report: personal insights, team insights, recommendations."""
    return meeting_analytics_service.build_intelligence_report(db, current_user)


@router.get("/personal-insights", response_model=Dict[str, Any])
async def get_personal_insights(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return personal meeting insights: time breakdown, action completion, speaking time, load trends."""
    report = meeting_analytics_service.build_intelligence_report(db, current_user)
    return report.get("personal_insights", {})


@router.get("/team-insights", response_model=Dict[str, Any])
async def get_team_insights(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return team meeting insights: efficiency, follow-through rates, low-value recurring meetings."""
    report = meeting_analytics_service.build_intelligence_report(db, current_user)
    return report.get("team_insights", {})


@router.get("/recommendations", response_model=List[str])
async def get_recommendations(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Return AI-generated meeting improvement recommendations."""
    report = meeting_analytics_service.build_intelligence_report(db, current_user)
    return report.get("recommendations", [])

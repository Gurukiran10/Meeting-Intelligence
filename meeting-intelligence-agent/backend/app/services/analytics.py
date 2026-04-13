"""
Analytics & Optimization Utilities
"""
from datetime import datetime, timedelta
from sqlalchemy import select, func, and_
from app.core.database import SessionLocal
from app.models.meeting import Meeting
from app.models.action_item import ActionItem
from app.models.user import User
from typing import Dict, List


def get_meeting_stats(user_id: str = None, days: int = 30) -> Dict:
    """
    Returns meeting stats for a user (or all users) over the last N days.
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        query = select(Meeting).where(Meeting.scheduled_start >= since)
        if user_id:
            query = query.where(Meeting.organizer_id == user_id)
        meetings = db.execute(query).scalars().all()
        total = len(meetings)
        with_action_items = len([m for m in meetings if m.action_items])
        with_decisions = len([m for m in meetings if getattr(m, "key_decisions", None)])
        avg_duration = sum(getattr(m, "duration_minutes", 0) or 0 for m in meetings) / total if total else 0
        return {
            "total_meetings": total,
            "meetings_with_action_items": with_action_items,
            "meetings_with_decisions": with_decisions,
            "avg_duration_minutes": avg_duration,
        }

def get_team_followthrough(user_ids: List[str], days: int = 30) -> Dict:
    """
    Returns follow-through rates for a team (action item completion).
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        query = select(ActionItem).where(ActionItem.created_at >= since, ActionItem.owner_id.in_(user_ids))
        items = db.execute(query).scalars().all()
        total = len(items)
        completed = len([i for i in items if i.status == "completed"])
        return {
            "total_action_items": total,
            "completed": completed,
            "completion_rate": (completed / total) * 100 if total else 0,
        }

def get_meeting_efficiency(user_id: str = None, days: int = 30) -> Dict:
    """
    Returns meeting efficiency stats (decisions/hour, action items/hour).
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        query = select(Meeting).where(Meeting.scheduled_start >= since)
        if user_id:
            query = query.where(Meeting.organizer_id == user_id)
        meetings = db.execute(query).scalars().all()
        total_hours = sum((getattr(m, "duration_minutes", 0) or 0) / 60 for m in meetings)
        total_decisions = sum(len(getattr(m, "key_decisions", [])) for m in meetings)
        total_actions = sum(len(m.action_items) for m in meetings)
        return {
            "decisions_per_hour": (total_decisions / total_hours) if total_hours else 0,
            "action_items_per_hour": (total_actions / total_hours) if total_hours else 0,
        }

def get_optimization_recommendations(user_id: str = None, days: int = 30) -> List[str]:
    """
    Returns recommendations for meeting optimization.
    """
    stats = get_meeting_stats(user_id, days)
    efficiency = get_meeting_efficiency(user_id, days)
    recs = []
    if stats["total_meetings"] > 0 and stats["meetings_with_decisions"] / stats["total_meetings"] < 0.5:
        recs.append("Many meetings lack decisions—consider canceling or restructuring.")
    if efficiency["decisions_per_hour"] < 1:
        recs.append("Low decision rate—try to focus meetings on outcomes.")
    if stats["avg_duration_minutes"] > 60:
        recs.append("Average meeting duration is high—consider shorter meetings.")
    return recs

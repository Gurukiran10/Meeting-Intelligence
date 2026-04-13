"""
Action Item Completion Tracking & Cross-Meeting Analysis
"""
from datetime import datetime, timedelta
from sqlalchemy import select, and_, func
from app.core.database import SessionLocal
from app.models.action_item import ActionItem
from app.models.user import User
from typing import Dict, List


def get_action_item_completion_stats(user_id: str = None, days: int = 30) -> Dict:
    """
    Returns completion stats for action items for a user (or all users) over the last N days.
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        query = select(ActionItem).where(ActionItem.created_at >= since)
        if user_id:
            query = query.where(ActionItem.owner_id == user_id)
        items = db.execute(query).scalars().all()
        total = len(items)
        completed = len([i for i in items if i.status == "completed"])
        overdue = len([i for i in items if i.status in ["open", "in_progress"] and i.due_date and i.due_date < now])
        blocked = len([i for i in items if i.status == "blocked"])
        recurring_incomplete = [i for i in items if i.status in ["open", "in_progress"] and i.reminder_count >= 3]
        return {
            "total": total,
            "completed": completed,
            "completion_rate": (completed / total) * 100 if total else 0,
            "overdue": overdue,
            "blocked": blocked,
            "recurring_incomplete": [str(i.id) for i in recurring_incomplete],
        }

def get_cross_meeting_action_patterns(user_id: str = None, days: int = 90) -> Dict:
    """
    Analyze action items across meetings for recurring blockers, overdue, and incomplete tasks.
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        query = select(ActionItem).where(ActionItem.created_at >= since)
        if user_id:
            query = query.where(ActionItem.owner_id == user_id)
        items = db.execute(query).scalars().all()
        blockers = {}
        for item in items:
            for blocked_id in (item.blocked_by or []):
                blockers.setdefault(blocked_id, []).append(str(item.id))
        chronically_overdue = [str(i.id) for i in items if i.status in ["open", "in_progress"] and i.due_date and (now - i.due_date).days > 14]
        return {
            "blockers": blockers,
            "chronically_overdue": chronically_overdue,
        }

"""
Absence Management & Catch-Up Service
"""
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from app.core.database import SessionLocal
from app.models.meeting import Meeting
from app.models.user import User
from app.models.mention import Mention
from app.models.action_item import ActionItem
from typing import Dict, Any, List

PRIORITY_MAP = {
    "decision_impact": "Critical",
    "action_assignment": "Critical",
    "question": "Important",
    "feedback": "FYI",
    "contextual": "FYI",
}


def generate_absence_catchup(user_id: str, meeting_id: str) -> Dict[str, Any]:
    """
    Generate a catch-up summary for a user who missed a meeting.
    """
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
        if not user or not meeting:
            return {"error": "User or meeting not found"}

        mentions = db.execute(
            select(Mention).where(
                and_(Mention.meeting_id == meeting_id, Mention.user_id == user_id)
            )
        ).scalars().all()
        actions = db.execute(
            select(ActionItem).where(
                and_(ActionItem.meeting_id == meeting_id, ActionItem.owner_id == user_id)
            )
        ).scalars().all()
        decisions = [m for m in mentions if m.is_decision]
        questions = [m for m in mentions if m.is_question]
        feedbacks = [m for m in mentions if m.is_feedback]
        priorities = set(PRIORITY_MAP.get(m.mention_type, "FYI") for m in mentions)
        prioritization = (
            "Critical" if "Critical" in priorities else
            "Important" if "Important" in priorities else
            "FYI"
        )
        summary = {
            "user": user.full_name,
            "meeting": meeting.title,
            "scheduled_start": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
            "mentions": [m.mentioned_text for m in mentions],
            "decisions": [m.mentioned_text for m in decisions],
            "actions": [{"title": a.title, "due_date": a.due_date.isoformat() if a.due_date else None} for a in actions],
            "questions": [m.mentioned_text for m in questions],
            "feedback": [m.mentioned_text for m in feedbacks],
            "prioritization": prioritization,
            "catchup_type": (
                "Full transcript with highlights" if prioritization == "Critical" else
                "Summary and highlights" if prioritization == "Important" else
                "FYI summary"
            ),
            "skip_recommendation": (
                "Should attend: critical decisions or actions" if prioritization == "Critical" else
                "Safe to skip: status only" if prioritization == "FYI" else
                "Recommended to review: important input needed"
            ),
        }
        return summary

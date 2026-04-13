"""
Collaborative Meeting Prep Utilities
"""
from datetime import datetime, timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.meeting import Meeting
from app.models.user import User
from typing import Dict, List


def check_agenda_presence(meeting_id: str) -> bool:
    """
    Returns True if the meeting has an agenda, False otherwise.
    """
    with SessionLocal() as db:
        meeting = db.get(Meeting, meeting_id)
        return bool(getattr(meeting, "agenda", None))

def suggest_agenda_items(meeting_id: str) -> List[str]:
    """
    Suggests agenda items based on previous meetings and context.
    """
    with SessionLocal() as db:
        meeting = db.get(Meeting, meeting_id)
        # Suggest unfinished action items, open decisions, or recurring topics
        suggestions = []
        if meeting and meeting.participant_ids:
            # Find recent meetings with same participants
            recent = db.execute(select(Meeting).where(Meeting.id != meeting_id, Meeting.scheduled_start < meeting.scheduled_start, Meeting.scheduled_start >= meeting.scheduled_start - timedelta(days=60))).scalars().all()
            for m in recent:
                for ai in getattr(m, "action_items", []):
                    if ai.status != "completed":
                        suggestions.append(f"Follow up: {ai.title}")
                for d in getattr(m, "key_decisions", []):
                    suggestions.append(f"Review decision: {d}")
        return suggestions

def optimize_attendees(meeting_id: str) -> Dict:
    """
    Suggests attendee changes based on agenda and past participation.
    """
    with SessionLocal() as db:
        meeting = db.get(Meeting, meeting_id)
        if not meeting or not getattr(meeting, "agenda", None):
            return {"add": [], "remove": []}
        # Example: Remove users with no agenda-relevant items, add users with open action items
        add = []
        remove = []
        for uid in meeting.participant_ids:
            user = db.get(User, uid)
            # Remove if user has no open action items or agenda topics
            has_relevant = False
            for ai in getattr(meeting, "action_items", []):
                if ai.owner_id == uid and ai.status != "completed":
                    has_relevant = True
            if not has_relevant:
                remove.append(uid)
        # Add users with open action items not already invited
        for ai in getattr(meeting, "action_items", []):
            if ai.owner_id not in meeting.participant_ids and ai.status != "completed":
                add.append(ai.owner_id)
        return {"add": add, "remove": remove}

def handle_no_agenda_workflow(meeting_id: str) -> str:
    """
    Returns a message or triggers workflow if no agenda is present.
    """
    if not check_agenda_presence(meeting_id):
        # Could trigger a Slack/notification or auto-cancel
        return "No agenda detected. Meeting may be auto-cancelled or flagged."
    return "Agenda present."

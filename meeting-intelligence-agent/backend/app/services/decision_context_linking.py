"""
Decision & Context Linking Utilities
"""
from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_
from app.core.database import SessionLocal
from app.models.meeting import Meeting
from app.models.mention import Mention
from app.models.action_item import ActionItem
from typing import Dict, List


def get_decision_graph(user_id: str = None, days: int = 90) -> Dict:
    """
    Returns a cross-meeting graph of decisions, blockers, and outcomes.
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        meetings = db.execute(select(Meeting).where(Meeting.scheduled_start >= since)).scalars().all()
        decisions = []
        blockers = {}
        outcomes = {}
        for meeting in meetings:
            for mention in getattr(meeting, "mentions", []):
                if mention.mention_type == "decision_impact":
                    decisions.append({
                        "meeting_id": str(meeting.id),
                        "decision": mention.mentioned_text,
                        "context": mention.full_context,
                        "date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
                    })
            for action in getattr(meeting, "action_items", []):
                for blocked_id in (action.blocked_by or []):
                    blockers.setdefault(blocked_id, []).append(str(action.id))
                if action.status == "completed":
                    outcomes[str(action.id)] = {
                        "meeting_id": str(meeting.id),
                        "completed_at": action.completed_at.isoformat() if action.completed_at else None,
                        "title": action.title,
                    }
        return {
            "decisions": decisions,
            "blockers": blockers,
            "outcomes": outcomes,
        }

def search_institutional_memory(query: str, days: int = 365) -> List[Dict]:
    """
    Search decision/action history for institutional memory.
    """
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    with SessionLocal() as db:
        mentions = db.execute(select(Mention).where(
            and_(Mention.created_at >= since, or_(Mention.mention_type == "decision_impact", Mention.is_decision == True))
        )).scalars().all()
        results = []
        for m in mentions:
            if query.lower() in (m.mentioned_text or '').lower():
                results.append({
                    "meeting_id": str(m.meeting_id),
                    "decision": m.mentioned_text,
                    "context": m.full_context,
                    "date": m.created_at.isoformat() if m.created_at else None,
                })
        return results

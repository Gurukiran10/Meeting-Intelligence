"""
Attendee context — maps name fragments to User records for the live intelligence layer.

Built once per meeting, reused for every segment.  Lets the intelligence layer:
  • resolve assignee strings ("Ajay") → user_id + full_name
  • check whether an event involves the session-owner (priority = critical)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class AttendeeInfo:
    user_id: str          # DB user UUID (str)
    display_name: str     # full name as stored in DB
    email: str = ""


class AttendeeMap:
    """
    Fast bidirectional lookup: name-part (≥3 chars) → AttendeeInfo.

    Example:
        map.resolve("Ajay")  # returns AttendeeInfo for "Ajay Kumar"
        map.resolve("kumar") # same result
    """

    def __init__(self, attendees: List[AttendeeInfo]) -> None:
        # part.lower() → first matching AttendeeInfo
        self._by_part: Dict[str, AttendeeInfo] = {}
        self._all: List[AttendeeInfo] = attendees
        for att in attendees:
            for part in att.display_name.lower().split():
                if len(part) >= 3 and part not in self._by_part:
                    self._by_part[part] = att

    def resolve(self, name: str) -> Optional[AttendeeInfo]:
        """Return the attendee whose name contains `name` (any part)."""
        for part in name.lower().split():
            if len(part) >= 3:
                hit = self._by_part.get(part)
                if hit:
                    return hit
        return None

    def all_names(self) -> List[str]:
        return [a.display_name for a in self._all]

    @classmethod
    def empty(cls) -> "AttendeeMap":
        return cls([])


# ── DB-backed builder (sync — call via run_in_executor) ──────────────────────

def build_attendee_map_sync(meeting_id: str, organization_id: str) -> AttendeeMap:
    """
    Fetch the meeting's attendees from DB and build an AttendeeMap.
    Returns an empty map silently if DB is unavailable.
    """
    try:
        from app.core.database import SessionLocal
        from app.models.meeting import Meeting
        from app.models.user import User

        db = SessionLocal()
        try:
            meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
            if not meeting:
                return AttendeeMap.empty()

            attendee_ids: List[str] = list(meeting.attendee_ids or [])
            if not attendee_ids:
                return AttendeeMap.empty()

            users = db.query(User).filter(User.id.in_(attendee_ids)).all()
            infos = [
                AttendeeInfo(
                    user_id=str(u.id),
                    display_name=u.full_name or u.username,
                    email=u.email or "",
                )
                for u in users
                if (u.full_name or u.username)
            ]
            return AttendeeMap(infos)
        finally:
            db.close()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug(
            "[user_context] build_attendee_map_sync failed (non-fatal): %s", exc
        )
        return AttendeeMap.empty()

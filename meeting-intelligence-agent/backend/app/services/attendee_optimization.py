"""
Attendee optimization — analyse who spoke vs who was invited.

After a meeting is transcribed we know:
  • which speakers appear in the transcript (speaker_id / speaker_name)
  • how long each spoke (sum of segment durations)
  • who was invited (meeting.attendee_ids + organizer)

We use this to surface insights like "12 people attended but only 3 spoke"
and suggest a smaller invite list for future meetings.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meeting import Meeting
from app.models.transcript import Transcript
from app.models.user import User

logger = logging.getLogger(__name__)


def _display_name(user: User) -> str:
    return (
        str(user.full_name or "").strip()
        or str(user.username or "").strip()
        or str(user.email or "").strip()
        or "Unknown"
    )


def _speaking_time_by_speaker(transcripts: List[Transcript]) -> Dict[str, float]:
    """Sum segment durations per speaker_id label."""
    totals: Dict[str, float] = {}
    for seg in transcripts:
        label = str(getattr(seg, "speaker_id", "") or "").strip()
        if not label:
            continue
        dur = float(getattr(seg, "duration", 0) or 0)
        if dur <= 0:
            end = float(getattr(seg, "end_time", 0) or 0)
            start = float(getattr(seg, "start_time", 0) or 0)
            dur = max(0.0, end - start)
        totals[label] = totals.get(label, 0.0) + dur
    return totals


def analyze_participation(db: Session, meeting: Meeting) -> Dict[str, Any]:
    """
    Return a participation breakdown for the meeting.

    Result shape:
    {
      "total_invited": int,
      "total_speakers": int,
      "silent_count": int,
      "total_duration_seconds": float,
      "speakers": [
          {"label": "Speaker A", "seconds": 120.5, "percent": 45.2},
          ...
      ],
      "silent_attendees": [
          {"user_id": "...", "name": "...", "email": "..."},
          ...
      ],
      "recommendation": str,          # human-readable advice
      "effectiveness_score": int,     # 0-100
    }
    """
    transcripts: List[Transcript] = db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting.id)
    ).scalars().all()

    speaking_map = _speaking_time_by_speaker(transcripts)
    total_duration = sum(speaking_map.values()) or 1.0  # avoid div-by-zero

    speakers = sorted(
        [
            {
                "label": label,
                "seconds": round(secs, 1),
                "percent": round(secs / total_duration * 100, 1),
            }
            for label, secs in speaking_map.items()
        ],
        key=lambda x: x["seconds"],
        reverse=True,
    )

    # Invited users
    invited_ids = list(meeting.attendee_ids or [])
    if meeting.organizer_id and str(meeting.organizer_id) not in [str(x) for x in invited_ids]:
        invited_ids.insert(0, str(meeting.organizer_id))

    invited_users: List[User] = []
    for uid in invited_ids:
        u = db.get(User, uid)
        if u:
            invited_users.append(u)

    total_invited = len(invited_users)
    total_speakers = len(speakers)

    # Identify silent attendees — invited but no transcript speaker label matches their name
    active_speaker_labels = {s["label"].lower() for s in speakers}

    silent_attendees = []
    for user in invited_users:
        name = _display_name(user).lower()
        email_prefix = str(user.email or "").split("@")[0].lower()
        username = str(user.username or "").lower()

        matched = any(
            any(part in label for part in [name, email_prefix, username] if part)
            for label in active_speaker_labels
        )
        if not matched and total_speakers > 0:
            silent_attendees.append({
                "user_id": str(user.id),
                "name": _display_name(user),
                "email": str(user.email or ""),
            })

    silent_count = len(silent_attendees)

    # Effectiveness score
    if total_invited == 0:
        effectiveness_score = 50
    else:
        participation_ratio = (total_invited - silent_count) / total_invited
        # Penalise if very large meeting with few speakers
        size_penalty = max(0, (total_invited - 6) * 3)
        effectiveness_score = max(0, min(100, int(participation_ratio * 100) - size_penalty))

    # Recommendation
    if total_invited == 0:
        recommendation = "No attendee data available."
    elif silent_count == 0:
        recommendation = "Great — everyone invited participated actively."
    elif silent_count == 1:
        recommendation = (
            f"1 attendee ({silent_attendees[0]['name']}) did not speak. "
            "Consider whether they need to attend or could receive a summary instead."
        )
    elif silent_count <= total_invited // 2:
        names = ", ".join(a["name"] for a in silent_attendees[:3])
        recommendation = (
            f"{silent_count} of {total_invited} attendees did not speak "
            f"({names}{', …' if silent_count > 3 else ''}). "
            "Consider a smaller invite list or sending them a summary."
        )
    else:
        recommendation = (
            f"Only {total_speakers} of {total_invited} attendees spoke. "
            "This meeting could be more effective with a smaller, more focused invite list."
        )

    return {
        "total_invited": total_invited,
        "total_speakers": total_speakers,
        "silent_count": silent_count,
        "total_duration_seconds": round(total_duration, 1),
        "speakers": speakers,
        "silent_attendees": silent_attendees,
        "recommendation": recommendation,
        "effectiveness_score": effectiveness_score,
    }

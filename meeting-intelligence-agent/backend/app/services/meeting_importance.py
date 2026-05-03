"""
Meeting skip / importance scoring service.

Scores each meeting for a specific user on a 0-100 scale using rule-based
signals derived entirely from data already in the DB.  No LLM calls needed.

Labels:
  critical  80-100  🔴  Must attend
  important 60-79   🟠  Should attend
  optional  35-59   🟡  Can skip
  skip       0-34   ⚪  Recommend skipping
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.user import User

logger = logging.getLogger(__name__)

# Keywords that signal a low-value recurring status dump
_STATUS_DUMP_KEYWORDS = {
    "standup", "stand-up", "stand up", "daily scrum", "weekly sync",
    "weekly update", "status update", "check-in", "check in", "catchup",
    "catch-up", "catch up", "sync up", "sync-up",
}

# Keywords that signal a high-value decision meeting
_HIGH_VALUE_KEYWORDS = {
    "decision", "review", "planning", "strategy", "roadmap", "launch",
    "kickoff", "kick-off", "proposal", "budget", "hiring", "architecture",
    "incident", "post-mortem", "retrospective", "okr", "sprint",
}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _agenda_text(meeting: Meeting) -> str:
    agenda = getattr(meeting, "agenda", None) or []
    if isinstance(agenda, list):
        return " ".join(str(item) for item in agenda).lower()
    if isinstance(agenda, dict):
        return " ".join(str(v) for v in agenda.values()).lower()
    return str(agenda).lower()


def _user_name_variants(user: User) -> List[str]:
    variants = []
    if user.full_name:
        variants.append(_text(user.full_name))
        parts = _text(user.full_name).split()
        if parts:
            variants.append(parts[0])
    if user.username:
        variants.append(_text(user.username))
    if user.email:
        variants.append(_text(user.email).split("@")[0])
    return [v for v in variants if v]


def _count_previous_meetings_with_group(
    db: Session,
    meeting: Meeting,
    user: User,
    lookback_days: int = 90,
) -> int:
    if not meeting.attendee_ids:
        return 0
    attendee_set = set(str(uid) for uid in meeting.attendee_ids)
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    past = db.execute(
        select(Meeting).where(
            and_(
                Meeting.organization_id == meeting.organization_id,
                Meeting.status == "completed",
                Meeting.scheduled_start >= cutoff,
                Meeting.id != meeting.id,
                Meeting.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    count = 0
    for m in past:
        past_attendees = set(str(uid) for uid in (m.attendee_ids or []))
        if len(past_attendees & attendee_set) >= max(1, len(attendee_set) // 2):
            count += 1
    return count


def _open_action_items_for_user(db: Session, user: User) -> int:
    result = db.execute(
        select(ActionItem).where(
            and_(
                ActionItem.assigned_to_user_id == user.id,
                ActionItem.status.in_(["open", "in_progress"]),
                ActionItem.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return len(result)


def score_meeting_for_user(
    db: Session,
    meeting: Meeting,
    user: User,
) -> Dict[str, Any]:
    """
    Return importance scoring dict for one user/meeting pair.
    Result is deterministic — call freely, cache in meeting_metadata.
    """
    score = 50  # neutral baseline
    reasons: List[str] = []
    warnings: List[str] = []

    attendee_ids = [str(uid) for uid in (meeting.attendee_ids or [])]
    user_id_str = str(user.id)
    is_organizer = str(getattr(meeting, "organizer_id", "")) == user_id_str
    is_attendee = user_id_str in attendee_ids
    attendee_count = len(attendee_ids) or 1
    title_text = _text(meeting.title)
    agenda_text = _agenda_text(meeting)
    full_text = f"{title_text} {agenda_text}"
    name_variants = _user_name_variants(user)

    # ── Organizer or sole attendee ─────────────────────────────────────────
    if is_organizer:
        score += 35
        reasons.append("You organised this meeting")

    # ── User mentioned in title or agenda ──────────────────────────────────
    for variant in name_variants:
        if variant and re.search(rf"\b{re.escape(variant)}\b", full_text):
            score += 20
            reasons.append("You are mentioned in the agenda or title")
            break

    # ── Meeting size signal ────────────────────────────────────────────────
    if attendee_count <= 3:
        score += 15
        reasons.append(f"Small focused meeting ({attendee_count} attendees)")
    elif attendee_count <= 6:
        score += 8
        reasons.append(f"Small team meeting ({attendee_count} attendees)")
    elif attendee_count >= 15:
        score -= 12
        warnings.append(f"Large group meeting ({attendee_count} attendees) — lower individual impact")
    elif attendee_count >= 10:
        score -= 6

    # ── High-value keyword in title ────────────────────────────────────────
    for kw in _HIGH_VALUE_KEYWORDS:
        if kw in full_text:
            score += 12
            reasons.append(f'Decision / planning meeting ("{kw}")')
            break

    # ── Status-dump / low-value pattern ───────────────────────────────────
    for kw in _STATUS_DUMP_KEYWORDS:
        if kw in full_text:
            score -= 10
            warnings.append(f'Recurring status sync ("{kw}") — consider async update')
            break

    # ── No agenda ─────────────────────────────────────────────────────────
    has_agenda = bool(getattr(meeting, "agenda", None))
    if not has_agenda:
        score -= 8
        warnings.append("No agenda set — meeting purpose unclear")

    # ── Short meeting (<= 30 min) ─────────────────────────────────────────
    duration = getattr(meeting, "duration_minutes", None)
    if not duration:
        try:
            delta = (meeting.scheduled_end - meeting.scheduled_start).total_seconds() / 60
            duration = int(delta)
        except Exception:
            duration = 60
    if duration and duration <= 20:
        score += 5
        reasons.append("Quick sync (≤20 min) — low time cost")
    elif duration and duration >= 90:
        score -= 5
        warnings.append(f"Long meeting ({duration} min) — ensure your input is needed")

    # ── First time meeting with this group ────────────────────────────────
    prev_meetings = _count_previous_meetings_with_group(db, meeting, user)
    if prev_meetings == 0:
        score += 10
        reasons.append("First meeting with this group — important for context")
    elif prev_meetings >= 5:
        score -= 5
        warnings.append("Frequent recurring meeting — evaluate if all attendees need to be present")

    # ── Open action items from prior meetings ─────────────────────────────
    open_items = _open_action_items_for_user(db, user)
    if open_items >= 3:
        score += 8
        reasons.append(f"You have {open_items} open action items — updates may be expected")

    # ── Not an attendee (invited but not on list) ─────────────────────────
    if not is_organizer and not is_attendee:
        score -= 15
        warnings.append("You are not listed as an attendee")

    score = max(0, min(100, score))

    # ── Label ─────────────────────────────────────────────────────────────
    if score >= 80:
        label = "critical"
        emoji = "🔴"
        recommendation = "You should attend — your presence or decisions are needed."
    elif score >= 60:
        label = "important"
        emoji = "🟠"
        recommendation = "Recommended to attend — you'll likely contribute or be impacted."
    elif score >= 35:
        label = "optional"
        emoji = "🟡"
        recommendation = "Optional — consider attending or reviewing the summary afterwards."
    else:
        label = "skip"
        emoji = "⚪"
        recommendation = "You can skip this one — ask for a summary if needed."

    if not reasons:
        reasons.append("Standard meeting invitation")

    return {
        "label": label,
        "score": score,
        "emoji": emoji,
        "recommendation": recommendation,
        "reasons": reasons,
        "warnings": warnings,
        "attendee_count": attendee_count,
        "duration_minutes": duration,
        "computed_at": datetime.utcnow().isoformat(),
    }

"""
Recurring pattern detection across meetings.

Detects:
  1. Silent attendees  — invited users who haven't spoken in 3+ meetings
  2. Chronic overdue   — action item owners with repeated overdue items
  3. Unresolved topics — discussion topics that recur across meetings with no decision
  4. Blocked items     — action items stuck in 'blocked' status for 2+ meetings
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
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


def _speaker_labels_for_meeting(db: Session, meeting_id: str) -> set[str]:
    segs = db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting_id)
    ).scalars().all()
    labels = set()
    for seg in segs:
        label = str(getattr(seg, "speaker_id", "") or getattr(seg, "speaker_name", "") or "").strip().lower()
        if label:
            labels.add(label)
    return labels


def _user_spoke_in(user: User, speaker_labels: set[str]) -> bool:
    name = _display_name(user).lower()
    email_prefix = str(user.email or "").split("@")[0].lower()
    username = str(user.username or "").lower()
    return any(
        any(part in label for part in [name, email_prefix, username] if part)
        for label in speaker_labels
    )


# ── Pattern 1: Silent Attendees ───────────────────────────────────────────────

def _detect_silent_attendees(
    db: Session,
    meetings: List[Meeting],
    org_users: Dict[str, User],
    threshold: int = 3,
) -> List[Dict[str, Any]]:
    """Users who were invited but never spoke in their last N meetings."""
    # completed meetings only — need transcripts
    completed = [m for m in meetings if str(getattr(m, "status", "")) == "completed"]
    completed_sorted = sorted(completed, key=lambda m: m.scheduled_start or datetime.min, reverse=True)

    # per-user: list of (meeting_id, title, date, spoke: bool)
    user_meeting_records: Dict[str, List[Dict]] = defaultdict(list)

    for meeting in completed_sorted:
        speaker_labels = _speaker_labels_for_meeting(db, str(meeting.id))
        invited_ids = list(meeting.attendee_ids or [])
        if meeting.organizer_id and str(meeting.organizer_id) not in [str(x) for x in invited_ids]:
            invited_ids.insert(0, str(meeting.organizer_id))

        for uid in invited_ids:
            user = org_users.get(str(uid))
            if not user:
                continue
            spoke = _user_spoke_in(user, speaker_labels) if speaker_labels else True
            user_meeting_records[str(uid)].append({
                "meeting_id": str(meeting.id),
                "title": meeting.title,
                "date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
                "spoke": spoke,
            })

    patterns = []
    for uid, records in user_meeting_records.items():
        # Only look at the last `threshold` meetings for this user
        recent = records[:threshold]
        if len(recent) < threshold:
            continue
        if all(not r["spoke"] for r in recent):
            user = org_users.get(uid)
            if not user:
                continue
            patterns.append({
                "type": "silent_attendee",
                "user_id": uid,
                "user_name": _display_name(user),
                "user_email": str(user.email or ""),
                "meetings_silent": len(recent),
                "meetings": [{"id": r["meeting_id"], "title": r["title"], "date": r["date"]} for r in recent],
                "recommendation": f"{_display_name(user)} hasn't spoken in the last {len(recent)} meetings. Consider sending them a summary instead of inviting them.",
                "severity": "warning",
            })

    return patterns


# ── Pattern 2: Chronic Overdue ────────────────────────────────────────────────

def _detect_chronic_overdue(
    db: Session,
    org_id: str,
    org_users: Dict[str, User],
    threshold: int = 3,
) -> List[Dict[str, Any]]:
    """Action item owners with 3+ overdue items across different meetings."""
    now = datetime.utcnow()
    overdue_items = db.execute(
        select(ActionItem).where(
            ActionItem.organization_id == org_id,
            ActionItem.status != "completed",
            ActionItem.status != "cancelled",
            ActionItem.due_date < now,
        )
    ).scalars().all()

    by_owner: Dict[str, List[ActionItem]] = defaultdict(list)
    for item in overdue_items:
        if item.assigned_to_user_id:
            by_owner[str(item.assigned_to_user_id)].append(item)

    patterns = []
    for uid, items in by_owner.items():
        if len(items) < threshold:
            continue
        user = org_users.get(uid)
        name = _display_name(user) if user else uid
        days_overdue = []
        item_details = []
        for item in items[:8]:
            delta = (now - item.due_date).days if item.due_date else 0
            days_overdue.append(delta)
            meeting = db.get(Meeting, item.meeting_id)
            item_details.append({
                "id": str(item.id),
                "title": item.title,
                "due_date": item.due_date.isoformat()[:10] if item.due_date else None,
                "days_overdue": delta,
                "status": item.status,
                "meeting_title": meeting.title if meeting else None,
            })

        avg_days = round(sum(days_overdue) / len(days_overdue), 0) if days_overdue else 0
        patterns.append({
            "type": "chronic_overdue",
            "user_id": uid,
            "user_name": name,
            "user_email": str(user.email or "") if user else "",
            "overdue_count": len(items),
            "avg_days_overdue": int(avg_days),
            "items": item_details,
            "recommendation": f"{name} has {len(items)} overdue action items (avg {int(avg_days)} days late). Review blockers or reassign.",
            "severity": "error" if len(items) >= 5 else "warning",
        })

    return sorted(patterns, key=lambda p: p["overdue_count"], reverse=True)


# ── Pattern 3: Unresolved Recurring Topics ────────────────────────────────────

def _normalize_topic(topic: str) -> str:
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is", "are", "was", "we", "our"}
    words = [w.lower().strip(".,!?:;-") for w in topic.split() if w.lower().strip(".,!?:;-") not in stop and len(w) > 2]
    return " ".join(sorted(words[:4]))


def _topics_overlap(t1: str, t2: str, min_shared: int = 2) -> bool:
    words1 = set(t1.split())
    words2 = set(t2.split())
    return len(words1 & words2) >= min_shared


def _detect_unresolved_topics(
    meetings: List[Meeting],
    threshold: int = 3,
) -> List[Dict[str, Any]]:
    """Discussion topics that appear in 3+ meetings without a recorded decision."""
    topic_occurrences: Dict[str, List[Dict]] = defaultdict(list)

    for meeting in meetings:
        topics = getattr(meeting, "discussion_topics", None) or []
        decisions_text = " ".join(
            str(d.get("decision", "") if isinstance(d, dict) else d)
            for d in (getattr(meeting, "key_decisions", None) or [])
        ).lower()

        for raw_topic in topics[:10]:
            text = str(raw_topic.get("topic", "") if isinstance(raw_topic, dict) else raw_topic).strip()
            if not text or len(text) < 5:
                continue
            key = _normalize_topic(text)
            if not key:
                continue
            resolved = any(w in decisions_text for w in key.split()[:2]) if decisions_text else False
            topic_occurrences[key].append({
                "meeting_id": str(meeting.id),
                "meeting_title": meeting.title,
                "date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
                "original_text": text,
                "resolved_in_meeting": resolved,
            })

    # Merge similar keys
    merged: Dict[str, List[Dict]] = {}
    keys = list(topic_occurrences.keys())
    used = set()
    for i, k1 in enumerate(keys):
        if k1 in used:
            continue
        group = list(topic_occurrences[k1])
        for k2 in keys[i + 1:]:
            if k2 not in used and _topics_overlap(k1, k2):
                group.extend(topic_occurrences[k2])
                used.add(k2)
        merged[k1] = group
        used.add(k1)

    patterns = []
    for key, occurrences in merged.items():
        if len(occurrences) < threshold:
            continue
        all_resolved = all(o["resolved_in_meeting"] for o in occurrences)
        if all_resolved:
            continue
        sample_text = occurrences[0]["original_text"]
        sorted_occ = sorted(occurrences, key=lambda o: o["date"] or "", reverse=True)
        patterns.append({
            "type": "unresolved_topic",
            "topic": sample_text,
            "occurrences": len(sorted_occ),
            "meetings": sorted_occ[:5],
            "last_seen": sorted_occ[0]["date"],
            "recommendation": f'"{sample_text}" has come up in {len(sorted_occ)} meetings without a clear decision. Consider making it a dedicated agenda item.',
            "severity": "info",
        })

    return sorted(patterns, key=lambda p: p["occurrences"], reverse=True)[:10]


# ── Pattern 4: Blocked Items ──────────────────────────────────────────────────

def _detect_blocked_items(
    db: Session,
    org_id: str,
    org_users: Dict[str, User],
    days_threshold: int = 7,
) -> List[Dict[str, Any]]:
    """Action items stuck in 'blocked' status for more than N days."""
    cutoff = datetime.utcnow() - timedelta(days=days_threshold)
    blocked_items = db.execute(
        select(ActionItem).where(
            ActionItem.organization_id == org_id,
            ActionItem.status == "blocked",
        )
    ).scalars().all()

    patterns = []
    for item in blocked_items:
        created = getattr(item, "created_at", None)
        if created and created > cutoff:
            continue  # blocked but recently — not a pattern yet
        user = org_users.get(str(item.assigned_to_user_id)) if item.assigned_to_user_id else None
        meeting = db.get(Meeting, item.meeting_id)
        days_blocked = (datetime.utcnow() - created).days if created else 0
        patterns.append({
            "type": "blocked_item",
            "item_id": str(item.id),
            "title": item.title,
            "owner_name": _display_name(user) if user else "Unassigned",
            "owner_id": str(item.assigned_to_user_id) if item.assigned_to_user_id else None,
            "days_blocked": days_blocked,
            "meeting_title": meeting.title if meeting else None,
            "meeting_id": str(item.meeting_id),
            "blocked_by": item.blocked_by or [],
            "recommendation": f'"{item.title}" has been blocked for {days_blocked} days. Escalate or break it down.',
            "severity": "warning" if days_blocked < 14 else "error",
        })

    return sorted(patterns, key=lambda p: p["days_blocked"], reverse=True)[:10]


# ── Public API ────────────────────────────────────────────────────────────────

def detect_patterns(db: Session, organization_id: str, lookback_days: int = 60) -> Dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=lookback_days)
    meetings = db.execute(
        select(Meeting).where(
            Meeting.organization_id == organization_id,
            Meeting.scheduled_start >= since,
            Meeting.deleted_at.is_(None),
        )
    ).scalars().all()

    org_users_list = db.execute(select(User).where(User.organization_id == organization_id)).scalars().all()
    org_users: Dict[str, User] = {str(u.id): u for u in org_users_list}

    silent = _detect_silent_attendees(db, meetings, org_users)
    chronic = _detect_chronic_overdue(db, organization_id, org_users)
    unresolved = _detect_unresolved_topics(meetings)
    blocked = _detect_blocked_items(db, organization_id, org_users)

    total = len(silent) + len(chronic) + len(unresolved) + len(blocked)

    return {
        "silent_attendees": silent,
        "chronic_overdue": chronic,
        "unresolved_topics": unresolved,
        "blocked_items": blocked,
        "summary": {
            "total_patterns": total,
            "silent_attendee_count": len(silent),
            "chronic_overdue_count": len(chronic),
            "unresolved_topic_count": len(unresolved),
            "blocked_item_count": len(blocked),
        },
        "analyzed_meetings": len(meetings),
        "lookback_days": lookback_days,
        "generated_at": datetime.utcnow().isoformat(),
    }

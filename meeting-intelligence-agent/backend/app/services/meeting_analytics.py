"""Meeting intelligence analytics helpers."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import or_, select, cast, String
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.user import User


def _text(value: Any) -> str:
    return str(value or "").strip()


class MeetingAnalyticsService:
    """Compute personal insights, team insights, and recommendations."""

    def build_intelligence_report(self, db: Session, current_user: User) -> Dict[str, Any]:
        meetings = self._load_user_meetings(db, current_user)
        action_items = list(db.execute(select(ActionItem).where(ActionItem.owner_id == getattr(current_user, "id", None))).scalars().all())
        organized_meetings = [m for m in meetings if getattr(m, "organizer_id", None) == getattr(current_user, "id", None)]

        personal = self._build_personal_insights(meetings, action_items, current_user)
        team = self._build_team_insights(db, organized_meetings)
        recommendations = self._build_recommendations(meetings, organized_meetings, action_items, team)

        return {
            "personal_insights": personal,
            "team_insights": team,
            "recommendations": recommendations,
            "generated_at": datetime.utcnow().isoformat(),
        }

    def _load_user_meetings(self, db: Session, current_user: User) -> List[Meeting]:
        return list(
            db.execute(
                select(Meeting).where(
                    or_(
                        Meeting.organizer_id == getattr(current_user, "id", None),
                        cast(Meeting.attendee_ids, String).ilike(f"%{getattr(current_user, 'id', '')}%"),
                    )
                )
            ).scalars().all()
        )

    def _build_personal_insights(self, meetings: List[Meeting], action_items: List[ActionItem], current_user: User) -> Dict[str, Any]:
        buckets: Dict[str, float] = defaultdict(float)
        total_minutes = 0.0
        speaking_minutes = 0.0
        weekly_load: Dict[str, Dict[str, float]] = defaultdict(lambda: {"meetings": 0.0, "hours": 0.0})

        for meeting in meetings:
            duration = self._meeting_duration_minutes(meeting)
            total_minutes += duration
            buckets[self._classify_meeting(meeting)] += duration

            meeting_speaking = getattr(meeting, "speaking_time", None) or {}
            user_speaking = 0.0
            if isinstance(meeting_speaking, dict):
                raw_value = meeting_speaking.get(str(getattr(current_user, "id", "")))
                try:
                    user_speaking = float(raw_value or 0.0)
                except Exception:
                    user_speaking = 0.0
            speaking_minutes += user_speaking

            date_value = getattr(meeting, "scheduled_start", None) or getattr(meeting, "created_at", None)
            if date_value is not None:
                week_label = f"{date_value.isocalendar().year}-W{date_value.isocalendar().week:02d}"
                weekly_load[week_label]["meetings"] += 1
                weekly_load[week_label]["hours"] += round(duration / 60.0, 2)

        completed = sum(1 for item in action_items if _text(getattr(item, "status", "")).lower() == "completed")
        completion_rate = round((completed / len(action_items)) * 100, 1) if action_items else 0.0

        return {
            "meeting_time_breakdown": {
                "strategic_minutes": round(buckets.get("strategic", 0.0), 1),
                "tactical_minutes": round(buckets.get("tactical", 0.0), 1),
                "status_minutes": round(buckets.get("status", 0.0), 1),
                "total_minutes": round(total_minutes, 1),
            },
            "action_completion_rate": completion_rate,
            "speaking_time": {
                "total_minutes": round(speaking_minutes, 1),
                "avg_minutes_per_meeting": round((speaking_minutes / len(meetings)), 1) if meetings else 0.0,
            },
            "meeting_load_trends": [
                {"week": week, "meetings": int(values["meetings"]), "hours": round(values["hours"], 1)}
                for week, values in sorted(weekly_load.items())[-8:]
            ],
        }

    def _build_team_insights(self, db: Session, meetings: List[Meeting]) -> Dict[str, Any]:
        total_minutes = sum(self._meeting_duration_minutes(meeting) for meeting in meetings)
        total_decisions = sum(len(getattr(meeting, "key_decisions", None) or []) for meeting in meetings)
        decisions_per_hour = round(total_decisions / (total_minutes / 60.0), 2) if total_minutes else 0.0

        meeting_ids = [getattr(meeting, "id", None) for meeting in meetings]
        action_items = []
        if meeting_ids:
            action_items = list(
                db.execute(select(ActionItem).where(ActionItem.meeting_id.in_(meeting_ids))).scalars().all()
            )

        follow_through_by_person: List[Dict[str, Any]] = []
        by_owner: Dict[str, List[ActionItem]] = defaultdict(list)
        for item in action_items:
            owner_id = getattr(item, "owner_id", None)
            if owner_id is not None:
                by_owner[str(owner_id)].append(item)

        for owner_id, items in by_owner.items():
            user = db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
            completed = sum(1 for item in items if _text(getattr(item, "status", "")).lower() == "completed")
            follow_through_by_person.append(
                {
                    "user_id": owner_id,
                    "user_name": _text(getattr(user, "full_name", "")) or _text(getattr(user, "username", "")) or owner_id,
                    "completion_rate": round((completed / len(items)) * 100, 1) if items else 0.0,
                    "open_items": sum(1 for item in items if _text(getattr(item, "status", "")).lower() != "completed"),
                }
            )

        low_value_recurring = self._low_value_recurring_meetings(meetings)
        return {
            "meeting_efficiency": {
                "decisions_per_hour": decisions_per_hour,
                "meetings_analyzed": len(meetings),
            },
            "follow_through_rates_by_person": sorted(follow_through_by_person, key=lambda item: item["completion_rate"]),
            "low_value_recurring_meetings": low_value_recurring,
        }

    def _build_recommendations(
        self,
        meetings: List[Meeting],
        organized_meetings: List[Meeting],
        action_items: List[ActionItem],
        team: Dict[str, Any],
    ) -> List[str]:
        recommendations: List[str] = []
        low_value = team.get("low_value_recurring_meetings", [])
        if low_value:
            recommendations.append(
                f"{len(low_value)} recurring meetings had zero decisions — consider canceling or converting them to async updates."
            )

        one_on_ones = [meeting for meeting in meetings if "1:1" in _text(getattr(meeting, "title", "")).lower() or "1on1" in _text(getattr(meeting, "title", "")).lower()]
        if one_on_ones:
            avg_1on1 = sum(self._meeting_duration_minutes(meeting) for meeting in one_on_ones) / len(one_on_ones)
            if avg_1on1 <= 20:
                recommendations.append(f"Your 1:1s average {round(avg_1on1)} minutes — shorten calendar slots to 15 or 20 minutes.")

        status_meetings = [meeting for meeting in meetings if self._classify_meeting(meeting) == "status"]
        if len(status_meetings) >= 3:
            recommendations.append("Several status-heavy meetings could be async. Share a written update before asking people to attend live.")

        overdue = sum(1 for item in action_items if getattr(item, "due_date", None) is not None and getattr(item, "due_date") < datetime.utcnow() and _text(getattr(item, "status", "")).lower() != "completed")
        if overdue >= 3:
            recommendations.append("Recurring overdue action items suggest follow-through risk. Add owners, deadlines, and blocked-state reviews.")

        if not recommendations and organized_meetings:
            recommendations.append("Meeting portfolio looks healthy. Keep emphasizing explicit decisions and owners for every next step.")
        return recommendations

    def _meeting_duration_minutes(self, meeting: Meeting) -> float:
        actual_start = getattr(meeting, "actual_start", None)
        actual_end = getattr(meeting, "actual_end", None)
        scheduled_start = getattr(meeting, "scheduled_start", None)
        scheduled_end = getattr(meeting, "scheduled_end", None)
        if actual_start is not None and actual_end is not None:
            return max((actual_end - actual_start).total_seconds() / 60.0, 0.0)
        if scheduled_start is not None and scheduled_end is not None:
            return max((scheduled_end - scheduled_start).total_seconds() / 60.0, 0.0)
        return float(getattr(meeting, "duration_minutes", 0.0) or 0.0)

    def _classify_meeting(self, meeting: Meeting) -> str:
        meeting_type = _text(getattr(meeting, "meeting_type", "")).lower()
        text = " ".join([
            _text(getattr(meeting, "title", "")).lower(),
            _text(getattr(meeting, "description", "")).lower(),
            " ".join(str(item).lower() for item in (getattr(meeting, "tags", None) or [])),
        ])
        if meeting_type in {"review", "planning", "strategy"} or any(keyword in text for keyword in ["strategy", "roadmap", "budget", "approval"]):
            return "strategic"
        if meeting_type in {"standup", "status"} or any(keyword in text for keyword in ["status", "standup", "update", "sync"]):
            return "status"
        return "tactical"

    def _low_value_recurring_meetings(self, meetings: List[Meeting]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Meeting]] = defaultdict(list)
        for meeting in meetings:
            title = _text(getattr(meeting, "title", ""))
            if title:
                grouped[title.lower()].append(meeting)

        low_value: List[Dict[str, Any]] = []
        for title, grouped_meetings in grouped.items():
            if len(grouped_meetings) < 3:
                continue
            zero_decisions = [meeting for meeting in grouped_meetings if len(getattr(meeting, "key_decisions", None) or []) == 0]
            if len(zero_decisions) == len(grouped_meetings):
                avg_duration = sum(self._meeting_duration_minutes(meeting) for meeting in grouped_meetings) / len(grouped_meetings)
                low_value.append(
                    {
                        "title": _text(getattr(grouped_meetings[0], "title", "")),
                        "count": len(grouped_meetings),
                        "avg_duration_minutes": round(avg_duration, 1),
                    }
                )
        return low_value[:10]


meeting_analytics_service = MeetingAnalyticsService()

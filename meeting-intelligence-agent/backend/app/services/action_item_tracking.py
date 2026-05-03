"""Action item tracking and follow-through helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.user import User
from app.services.integrations.linear import linear_service
from app.services.integrations.slack import slack_service

logger = logging.getLogger(__name__)

from app.core.config import settings as _settings
APP_BASE_URL = _settings.FRONTEND_URL


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> Optional[str]:
    text = _text(value)
    return text or None


class ActionItemTrackingService:
    """Service for extracting, tracking, reminding, and syncing action items."""

    async def create_action_items_from_meeting(
        self,
        db: Session,
        meeting: Meeting,
        extracted_items: List[Dict[str, Any]],
    ) -> List[ActionItem]:
        """Create missing action items from meeting extraction output."""
        created_items: List[ActionItem] = []
        meeting_id = getattr(meeting, "id", None)
        meeting_title = _text(getattr(meeting, "title", "Meeting"))

        for item_data in extracted_items:
            title = _text(item_data.get("task") or item_data.get("title"))
            if not title:
                continue

            existing = db.execute(
                select(ActionItem).where(
                    ActionItem.meeting_id == meeting_id,
                    ActionItem.title == title,
                )
            ).scalar_one_or_none()
            if existing:
                created_items.append(existing)
                continue

            owner = await self._find_user_by_name_or_email(db, _text(item_data.get("owner")))
            due_date = self._parse_deadline(_optional_text(item_data.get("deadline") or item_data.get("due_date")))
            priority = _text(item_data.get("priority") or "medium").lower() or "medium"

            action_item = ActionItem(
                meeting_id=meeting_id,
                title=title,
                description=_optional_text(item_data.get("description")),
                owner_id=getattr(owner, "id", None),
                due_date=due_date,
                priority=priority,
                status="open",
                extraction_method="ai_detected",
                confidence_score=float(item_data.get("confidence") or 0.75),
                item_metadata={
                    "urgency": _text(item_data.get("urgency") or priority),
                    "context_dependencies": item_data.get("context_dependencies") or [],
                    "source": "meeting_extraction",
                    "meeting_title": meeting_title,
                },
            )
            db.add(action_item)
            db.flush()
            created_items.append(action_item)

        if created_items:
            db.commit()
            for item in created_items:
                db.refresh(item)
            await self._sync_to_external_systems(db, created_items)

        return created_items

    async def _find_user_by_name_or_email(self, db: Session, identifier: str) -> Optional[User]:
        """Find a user by email, full name, username, or first name."""
        normalized = identifier.strip().lower()
        if not normalized:
            return None

        user = db.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
        if user:
            return user

        users = db.execute(select(User).where(User.is_active.is_(True))).scalars().all()
        for user_row in users:
            full_name = _text(getattr(user_row, "full_name", "")).lower()
            username = _text(getattr(user_row, "username", "")).lower()
            email_name = _text(getattr(user_row, "email", "")).split("@")[0].lower()
            first_name = full_name.split()[0] if full_name else ""
            aliases = {alias for alias in [full_name, username, email_name, first_name] if alias}
            if normalized in aliases:
                return user_row
        return None

    def _parse_deadline(self, deadline_str: Optional[str]) -> Optional[datetime]:
        """Parse simple natural-language deadline hints."""
        if not deadline_str:
            return None

        normalized = deadline_str.lower().strip()
        now = datetime.utcnow()

        try:
            return datetime.fromisoformat(deadline_str)
        except Exception:
            pass

        if "tomorrow" in normalized:
            return now + timedelta(days=1)
        if "next week" in normalized:
            return now + timedelta(days=7)
        if "end of week" in normalized or normalized == "eow":
            return now + timedelta(days=(4 - now.weekday()) % 7)
        if "by friday" in normalized:
            return now + timedelta(days=(4 - now.weekday()) % 7)
        if "by monday" in normalized:
            delta = (7 - now.weekday()) % 7
            return now + timedelta(days=delta or 7)
        if normalized == "eom" or "end of month" in normalized:
            next_month = now.replace(day=28) + timedelta(days=4)
            return next_month - timedelta(days=next_month.day)
        return None

    async def _sync_to_external_systems(self, db: Session, action_items: List[ActionItem]) -> None:
        """Create tasks in external systems where configured."""
        teams_cache: Optional[List[Dict[str, Any]]] = None

        for item in action_items:
            owner_id = getattr(item, "owner_id", None)
            if owner_id is None:
                continue

            owner = db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
            if not owner:
                continue

            linear_config = dict((getattr(owner, "integrations", None) or {}).get("linear", {}))
            api_key = _optional_text(linear_config.get("api_key") or getattr(linear_service, "api_key", ""))
            if not api_key:
                continue

            original_api_key = linear_service.api_key
            linear_service.api_key = api_key
            try:
                if teams_cache is None:
                    teams_cache = await linear_service.get_teams()
                if not teams_cache:
                    continue

                created_issue = await linear_service.create_issue(
                    title=_text(getattr(item, "title", "Action Item")),
                    description=_text(getattr(item, "description", "Generated from meeting follow-through.")),
                    team_id=str(teams_cache[0].get("id")),
                    due_date=(lambda d: d.date().isoformat() if d is not None else None)(getattr(item, "due_date", None)),
                )
                if created_issue:
                    item.external_task_id = created_issue.get("id")  # type: ignore[attr-defined]
                    item.external_task_url = created_issue.get("url")  # type: ignore[attr-defined]
                    item.integration_type = "linear"  # type: ignore[attr-defined]
                    item.sync_status = "synced"  # type: ignore[attr-defined]
                    db.commit()
            except Exception as exc:
                logger.warning("Failed external sync for action item %s: %s", getattr(item, "id", None), exc)
            finally:
                linear_service.api_key = original_api_key

    async def send_reminders(self, db: Session) -> Dict[str, int]:
        """Send 48h, day-of, and overdue reminders."""
        now = datetime.utcnow()

        reminder_48h = list(
            db.execute(
                select(ActionItem).where(
                    and_(
                        ActionItem.status.in_(["open", "in_progress", "blocked"]),
                        ActionItem.due_date.is_not(None),
                        ActionItem.due_date <= now + timedelta(hours=48),
                        ActionItem.due_date > now + timedelta(hours=24),
                        ActionItem.reminder_sent_48h.is_(False),
                    )
                )
            ).scalars().all()
        )
        due_today = list(
            db.execute(
                select(ActionItem).where(
                    and_(
                        ActionItem.status.in_(["open", "in_progress", "blocked"]),
                        ActionItem.due_date.is_not(None),
                        func.date(ActionItem.due_date) == now.date(),
                        ActionItem.reminder_sent_24h.is_(False),
                    )
                )
            ).scalars().all()
        )
        overdue = list(
            db.execute(
                select(ActionItem).where(
                    and_(
                        ActionItem.status.in_(["open", "in_progress", "blocked"]),
                        ActionItem.due_date.is_not(None),
                        ActionItem.due_date < now,
                        ActionItem.reminder_sent_overdue.is_(False),
                    )
                )
            ).scalars().all()
        )

        await self._send_reminder_batch(db, reminder_48h, "48h_before_deadline")
        await self._send_reminder_batch(db, due_today, "due_today")
        await self._send_reminder_batch(db, overdue, "overdue")

        return {
            "48h_reminders": len(reminder_48h),
            "due_today": len(due_today),
            "overdue": len(overdue),
        }

    async def _send_reminder_batch(self, db: Session, action_items: List[ActionItem], reminder_type: str) -> None:
        for item in action_items:
            await self._send_reminder_for_item(db, item, reminder_type)

    async def _send_reminder_for_item(self, db: Session, action_item: ActionItem, reminder_type: str) -> None:
        owner_id = getattr(action_item, "owner_id", None)
        if owner_id is None:
            return

        owner = db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
        if not owner:
            return

        slack_settings = dict((getattr(owner, "integrations", None) or {}).get("slack", {}))
        bot_token = _optional_text(slack_settings.get("bot_token"))
        recipient_email = _optional_text(getattr(owner, "email", ""))
        if not bot_token or not recipient_email:
            return

        due_date = getattr(action_item, "due_date", None)
        due_label = due_date.strftime("%Y-%m-%d") if due_date is not None else "Not set"
        urgency_label = {
            "48h_before_deadline": "Important",
            "due_today": "Critical",
            "overdue": "Escalated",
        }.get(reminder_type, "Reminder")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"⏰ {urgency_label}: {_text(getattr(action_item, 'title', 'Action Item'))}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Due:*\n{due_label}"},
                    {"type": "mrkdwn", "text": f"*Priority:*\n{_text(getattr(action_item, 'priority', 'medium')).title()}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _text(getattr(action_item, "description", "No additional context provided."))},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open Action Item"},
                        "url": f"{APP_BASE_URL}/action-items/{getattr(action_item, 'id', '')}",
                        "style": "primary",
                    }
                ],
            },
        ]

        await slack_service.send_blocks_via_token(
            bot_token=bot_token,
            recipient_email=recipient_email,
            text=f"Action reminder: {_text(getattr(action_item, 'title', 'Action Item'))}",
            blocks=blocks,
        )

        if reminder_type == "48h_before_deadline":
            action_item.reminder_sent_48h = True  # type: ignore[attr-defined]
        elif reminder_type == "due_today":
            action_item.reminder_sent_24h = True  # type: ignore[attr-defined]
        elif reminder_type == "overdue":
            action_item.reminder_sent_overdue = True  # type: ignore[attr-defined]
        action_item.reminder_count = int(getattr(action_item, "reminder_count", 0) or 0) + 1  # type: ignore[attr-defined]
        db.commit()

    async def check_completion_status(self, db: Session) -> Dict[str, Any]:
        """Inspect completion, overdue risk, and recurring blockers."""
        now = datetime.utcnow()
        open_items = list(
            db.execute(
                select(ActionItem).where(ActionItem.status.in_(["open", "in_progress", "blocked"]))
            ).scalars().all()
        )
        overdue_items = [item for item in open_items if getattr(item, "due_date", None) is not None and getattr(item, "due_date") < now]
        flagged_items = [
            {
                "id": str(getattr(item, "id", "")),
                "title": _text(getattr(item, "title", "")),
                "owner_id": str(getattr(item, "owner_id", "")) if getattr(item, "owner_id", None) is not None else None,
                "days_overdue": (now - getattr(item, "due_date")).days if getattr(item, "due_date", None) is not None else None,
            }
            for item in overdue_items
            if getattr(item, "due_date", None) is not None and getattr(item, "due_date") < now - timedelta(days=7)
        ]

        return {
            "total_open": len(open_items),
            "overdue_count": len(overdue_items),
            "chronically_incomplete_users": await self._identify_chronically_incomplete(db),
            "cross_meeting_patterns": await self._analyze_cross_meeting_patterns(db),
            "flagged_items": flagged_items,
        }

    async def _identify_chronically_incomplete(self, db: Session) -> List[Dict[str, Any]]:
        rows = db.execute(
            select(ActionItem.owner_id, func.count(ActionItem.id))
            .where(
                and_(
                    ActionItem.owner_id.is_not(None),
                    ActionItem.status.in_(["open", "in_progress", "blocked"]),
                    ActionItem.due_date.is_not(None),
                    ActionItem.due_date < datetime.utcnow(),
                )
            )
            .group_by(ActionItem.owner_id)
            .having(func.count(ActionItem.id) >= 3)
        ).all()

        results: List[Dict[str, Any]] = []
        for owner_id, overdue_count in rows:
            user = db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
            if not user:
                continue
            results.append(
                {
                    "user_id": str(getattr(user, "id", "")),
                    "user_name": _text(getattr(user, "full_name", "")) or _text(getattr(user, "username", "")),
                    "overdue_count": int(overdue_count or 0),
                }
            )
        return results

    async def _analyze_cross_meeting_patterns(self, db: Session) -> List[Dict[str, Any]]:
        open_items = list(
            db.execute(
                select(ActionItem).where(ActionItem.status.in_(["open", "in_progress", "blocked"]))
            ).scalars().all()
        )
        grouped: Dict[str, List[ActionItem]] = {}
        for item in open_items:
            key = _text(getattr(item, "title", "")).lower()
            if not key:
                continue
            grouped.setdefault(key, []).append(item)

        patterns: List[Dict[str, Any]] = []
        for _, items in grouped.items():
            if len(items) < 2:
                continue
            blocked_count = sum(1 for item in items if _text(getattr(item, "status", "")) == "blocked")
            patterns.append(
                {
                    "task_title": _text(getattr(items[0], "title", "")),
                    "occurrences": len(items),
                    "meeting_ids": [str(getattr(item, "meeting_id", "")) for item in items],
                    "blocked_count": blocked_count,
                    "pattern_type": "blocked_recurring" if blocked_count else "recurring_incomplete",
                }
            )
        return patterns[:10]

    async def update_action_item_status(
        self,
        db: Session,
        action_item_id: str,
        new_status: str,
        updated_by: str,
    ) -> bool:
        """Update status locally and best-effort sync externally."""
        action_item = db.execute(select(ActionItem).where(ActionItem.id == action_item_id)).scalar_one_or_none()
        if not action_item:
            return False

        action_item.status = new_status  # type: ignore[attr-defined]
        action_item.updated_at = datetime.utcnow()  # type: ignore[attr-defined]
        metadata = dict(getattr(action_item, "item_metadata", None) or {})
        metadata["last_updated_by"] = updated_by
        action_item.item_metadata = metadata  # type: ignore[attr-defined]
        if new_status == "completed":
            action_item.completed_at = datetime.utcnow()  # type: ignore[attr-defined]
        db.commit()

        owner_id = getattr(action_item, "owner_id", None)
        task_id = _optional_text(getattr(action_item, "external_task_id", None))
        integration_type = _optional_text(getattr(action_item, "integration_type", None))
        if owner_id is not None and task_id and integration_type == "linear":
            owner = db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
            if owner:
                linear_config = dict((getattr(owner, "integrations", None) or {}).get("linear", {}))
                api_key = _optional_text(linear_config.get("api_key") or getattr(linear_service, "api_key", ""))
                done_state_id = _optional_text(linear_config.get("completed_state_id"))
                if api_key and done_state_id:
                    original_api_key = linear_service.api_key
                    linear_service.api_key = api_key
                    try:
                        await linear_service.update_issue_status(task_id, done_state_id)
                    except Exception as exc:
                        logger.warning("Failed to update Linear status for %s: %s", action_item_id, exc)
                    finally:
                        linear_service.api_key = original_api_key
        return True


action_item_tracking_service = ActionItemTrackingService()
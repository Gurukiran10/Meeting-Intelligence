"""
Action Item Reminder and Tracking Tasks
"""
from datetime import datetime, timedelta
from celery import shared_task
from sqlalchemy import select, and_
from app.core.database import SessionLocal
from app.models.action_item import ActionItem
from app.models.user import User
from app.services.integrations.slack import slack_service
import logging

logger = logging.getLogger(__name__)

@shared_task(name="send_action_item_reminders")
def send_action_item_reminders():
    """
    Send reminders for action items:
    - 48h before due date
    - Day of due date
    - Overdue
    """
    now = datetime.utcnow()
    with SessionLocal() as db:
        # 48h reminders
        items_48h = db.execute(
            select(ActionItem).where(
                and_(
                    ActionItem.status.in_(["open", "in_progress"]),
                    ActionItem.due_date != None,
                    ActionItem.due_date > now,
                    ActionItem.due_date <= now + timedelta(hours=48),
                    ActionItem.reminder_sent_48h == False,
                )
            )
        ).scalars().all()
        for item in items_48h:
            _send_reminder(db, item, "48h")
            item.reminder_sent_48h = True
            item.reminder_count += 1
        # Day-of reminders
        items_dayof = db.execute(
            select(ActionItem).where(
                and_(
                    ActionItem.status.in_(["open", "in_progress"]),
                    ActionItem.due_date != None,
                    ActionItem.due_date.date() == now.date(),
                    ActionItem.reminder_sent_24h == False,
                )
            )
        ).scalars().all()
        for item in items_dayof:
            _send_reminder(db, item, "day-of")
            item.reminder_sent_24h = True
            item.reminder_count += 1
        # Overdue reminders
        items_overdue = db.execute(
            select(ActionItem).where(
                and_(
                    ActionItem.status.in_(["open", "in_progress"]),
                    ActionItem.due_date != None,
                    ActionItem.due_date < now,
                    ActionItem.reminder_sent_overdue == False,
                )
            )
        ).scalars().all()
        for item in items_overdue:
            _send_reminder(db, item, "overdue")
            item.reminder_sent_overdue = True
            item.reminder_count += 1
        db.commit()

def _send_reminder(db, item, reminder_type):
    owner = db.execute(select(User).where(User.id == item.owner_id)).scalar_one_or_none()
    if not owner:
        return
    slack_settings = dict((owner.integrations or {}).get("slack", {}))
    if slack_settings.get("bot_token"):
        try:
            slack_service.send_action_item_reminder(
                bot_token=slack_settings["bot_token"],
                recipient_email=owner.email,
                action_item_id=str(item.id),
                title=item.title,
                due_date=item.due_date.isoformat() if item.due_date else None,
                reminder_type=reminder_type,
            )
        except Exception as exc:
            logger.warning(f"Failed to send Slack reminder for action item {item.id}: {exc}")

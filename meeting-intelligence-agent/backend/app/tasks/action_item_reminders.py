"""
Action Item Reminder and Tracking Tasks
"""
import asyncio
import logging
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy import and_, select, func

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.action_item import ActionItem
from app.models.user import User
from app.services.integrations.slack import slack_service

logger = logging.getLogger(__name__)


@shared_task(name="send_action_item_reminders")
def send_action_item_reminders():
    """
    Send reminders for action items:
    - 48h before due date
    - Day of due date
    - Overdue
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _run_reminders())
                future.result()
        else:
            loop.run_until_complete(_run_reminders())
    except RuntimeError:
        asyncio.run(_run_reminders())


async def _run_reminders():
    now = datetime.utcnow()
    sent = skipped = 0

    with SessionLocal() as db:
        buckets = [
            (
                "48h",
                select(ActionItem).where(
                    and_(
                        ActionItem.status.in_(["open", "in_progress"]),
                        ActionItem.due_date.isnot(None),
                        ActionItem.due_date > now,
                        ActionItem.due_date <= now + timedelta(hours=48),
                        ActionItem.reminder_sent_48h == False,
                    )
                ),
                "reminder_sent_48h",
            ),
            (
                "day-of",
                select(ActionItem).where(
                    and_(
                        ActionItem.status.in_(["open", "in_progress"]),
                        ActionItem.due_date.isnot(None),
                        func.date(ActionItem.due_date) == func.date(now),
                        ActionItem.reminder_sent_24h == False,
                    )
                ),
                "reminder_sent_24h",
            ),
            (
                "overdue",
                select(ActionItem).where(
                    and_(
                        ActionItem.status.in_(["open", "in_progress"]),
                        ActionItem.due_date.isnot(None),
                        ActionItem.due_date < now,
                        ActionItem.reminder_sent_overdue == False,
                    )
                ),
                "reminder_sent_overdue",
            ),
        ]

        for reminder_type, query, sent_flag in buckets:
            items = db.execute(query).scalars().all()
            for item in items:
                ok = await _deliver_reminder(db, item, reminder_type)
                if ok:
                    setattr(item, sent_flag, True)
                    item.reminder_count = (item.reminder_count or 0) + 1
                    sent += 1
                else:
                    skipped += 1

        db.commit()

    logger.info("action_reminders: sent=%d skipped=%d", sent, skipped)


async def _deliver_reminder(db, item: ActionItem, reminder_type: str) -> bool:
    owner = db.execute(select(User).where(User.id == item.owner_id)).scalar_one_or_none()
    if not owner:
        return False

    due_str = item.due_date.isoformat() if item.due_date else None
    priority = str(item.priority or "medium")
    type_labels = {"48h": "due in 48 hours", "day-of": "due TODAY", "overdue": "OVERDUE"}
    emoji = {"48h": "🟡", "day-of": "🟠", "overdue": "🔴"}.get(reminder_type, "⏰")
    label = type_labels.get(reminder_type, reminder_type)

    slack_ok = False
    email_ok = False
    slack_settings = dict((owner.integrations or {}).get("slack", {}))
    bot_token = slack_settings.get("bot_token")
    recipient_email = str(owner.email or "")

    if bot_token and recipient_email:
        blocks = _build_reminder_blocks(item, reminder_type, emoji, label, due_str, priority)
        try:
            await slack_service.send_blocks_via_token(
                bot_token=bot_token,
                recipient_email=recipient_email,
                text=f"{emoji} Action item {label}: {item.title}",
                blocks=blocks,
            )
            slack_ok = True
            logger.info("reminder: Slack sent (%s) for item %s to %s", reminder_type, item.id, recipient_email)
        except Exception as exc:
            logger.warning("reminder: Slack failed for %s: %s", recipient_email, exc)
    elif not bot_token:
        logger.debug("reminder: no Slack bot_token for user %s — email only", owner.id)

    # Email fallback / parallel
    if recipient_email:
        try:
            from app.services.email_service import email_service
            email_ok = await email_service.send_action_item_reminder(
                recipient_email=recipient_email,
                title=str(item.title),
                due_date=due_str,
                priority=priority,
                reminder_type=reminder_type,
                action_item_id=str(item.id),
            )
            if email_ok:
                logger.info("reminder: email sent (%s) for item %s to %s", reminder_type, item.id, recipient_email)
            else:
                logger.warning("reminder: email not delivered for %s (RESEND_API_KEY set? %s)", recipient_email, bool(getattr(settings, 'RESEND_API_KEY', '')))
        except Exception as exc:
            logger.warning("reminder: email failed for %s: %s", recipient_email, exc)

    delivered = slack_ok or email_ok
    if not delivered:
        logger.warning("reminder: no channel delivered for item %s owner %s (slack=%s email=%s)", item.id, owner.id, slack_ok, email_ok)
    return delivered


def _build_reminder_blocks(item: ActionItem, reminder_type: str, emoji: str, label: str, due_str, priority: str) -> list:
    fields = [{"type": "mrkdwn", "text": f"*Priority:*\n{priority.upper()}"}]
    if due_str:
        fields.append({"type": "mrkdwn", "text": f"*Due:*\n{due_str[:10]}"})

    colour_map = {"urgent": "danger", "high": "danger", "medium": "warning"}
    colour = colour_map.get(priority.lower(), "good")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} Action Item {label.title()}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{item.title}*"},
        },
    ]

    if fields:
        blocks.append({"type": "section", "fields": fields})

    if item.description:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{str(item.description)[:300]}_"},
        })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "View Task"},
                "url": f"{settings.FRONTEND_URL}/action-items/{item.id}",
                "style": "primary",
            }
        ],
    })

    return blocks

"""
Celery beat task: generates and delivers pre-meeting briefs 30 minutes before
each scheduled meeting. Runs every 5 minutes; uses meeting_metadata to ensure
each user receives exactly one brief per meeting.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy import select, and_
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.meeting import Meeting
from app.models.user import User
from app.services.notifications import create_notification

logger = logging.getLogger(__name__)

BRIEF_WINDOW_MINUTES = 30   # send brief this many minutes before start
BRIEF_EARLY_MINUTES  = 35   # don't send earlier than this (avoids double-send on restart)


async def _deliver_brief(meeting: Meeting, user: User, db) -> bool:
    """Generate and deliver a brief for one user/meeting pair. Returns True on success."""
    from app.services.pre_meeting_briefs import pre_meeting_brief_service
    from app.services.integrations.slack import slack_service

    try:
        brief = await pre_meeting_brief_service.generate_brief_for_user(db, meeting, user)
    except Exception as exc:
        logger.warning("pre_brief: generation failed for user=%s meeting=%s: %s", user.id, meeting.id, exc)
        return False

    # Build the notification message (plain text for the bell icon)
    agenda_topics = pre_meeting_brief_service._agenda_topics(meeting)
    agenda_line = f"\n📋 Agenda: {', '.join(agenda_topics[:3])}" if agenda_topics else ""

    open_items = brief.get("your_preparation", {}).get("open_action_items", [])
    items_line = ""
    if open_items:
        titles = ", ".join(i["title"] for i in open_items[:2])
        items_line = f"\n⚡ Your open tasks: {titles}"

    last_mtg = brief.get("meeting_context", {}).get("last_group_meeting")
    history_line = ""
    if last_mtg and last_mtg.get("date"):
        history_line = f"\n🕐 Last met: {last_mtg['date'][:10]}"
    elif not last_mtg:
        history_line = "\n🆕 First meeting with this group"

    time_opt = brief.get("time_optimization", "")
    opt_emoji = "🔴" if "Critical" in time_opt else "🟡"

    message = (
        f"📋 Pre-Meeting Brief: {meeting.title}\n"
        f"🕐 Starts at: {meeting.scheduled_start.strftime('%H:%M')}"
        f"{agenda_line}{items_line}{history_line}\n"
        f"{opt_emoji} {time_opt}\n"
        f"🔗 {settings.FRONTEND_URL}/meetings/{meeting.id}"
    )

    # 1. Always create in-app notification
    create_notification(
        db,
        user_id=user.id,
        organization_id=meeting.organization_id,
        notification_type="pre_meeting_brief",
        message=message,
        notification_metadata={
            "meeting_id": str(meeting.id),
            "brief": brief,
        },
    )

    # 2. Slack DM if connected
    slack_creds = (user.integrations or {}).get("slack", {})
    bot_token = slack_creds.get("bot_token")
    if bot_token and user.email:
        try:
            blocks = _build_brief_blocks(meeting, brief, message)
            await slack_service.send_blocks_via_token(
                bot_token=bot_token,
                recipient_email=user.email,
                text=f"📋 Pre-Meeting Brief: {meeting.title}",
                blocks=blocks,
            )
            logger.info("pre_brief: Slack DM sent to %s for meeting %s", user.email, meeting.id)
        except Exception as exc:
            logger.warning("pre_brief: Slack DM failed for %s (non-fatal): %s", user.email, exc)
    elif user.slack_user_id:
        # Fallback: plain-text DM using global token
        from app.services.slack_service import send_slack_dm
        token = getattr(settings, "SLACK_BOT_TOKEN", None)
        send_slack_dm(user.slack_user_id, message, bot_token=token)

    return True


def _build_brief_blocks(meeting: Meeting, brief: dict, fallback_text: str) -> list:
    """Build Slack Block Kit blocks for the pre-meeting brief."""
    start_str = meeting.scheduled_start.strftime("%B %d, %Y at %H:%M UTC")
    time_opt = brief.get("time_optimization", "")
    opt_emoji = "🔴" if "Critical" in time_opt else "🟡"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 Pre-Meeting Brief"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{meeting.title}*\n🕐 {start_str}"},
        },
        {"type": "divider"},
    ]

    # Last meeting with this group
    mtg_ctx = brief.get("meeting_context", {})
    last_mtg = mtg_ctx.get("last_group_meeting")
    if last_mtg and last_mtg.get("summary"):
        summary_snip = (last_mtg["summary"] or "")[:200]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🕐 Last time this group met ({last_mtg['date'][:10]}):*\n>{summary_snip}"},
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "🆕 *First meeting with this group* — no prior history."},
        })

    # Agenda
    from app.services.pre_meeting_briefs import pre_meeting_brief_service
    agenda_topics = pre_meeting_brief_service._agenda_topics(meeting)
    if agenda_topics:
        topic_lines = "\n".join(f"• {t}" for t in agenda_topics[:5])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📋 Agenda:*\n{topic_lines}"},
        })

    # Your open action items
    open_items = brief.get("your_preparation", {}).get("open_action_items", [])
    if open_items:
        item_lines = "\n".join(
            f"• {i['title']}" + (f" _(due {i['due_date'][:10]})_" if i.get("due_date") else "")
            for i in open_items[:4]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*⚡ Your open action items:*\n{item_lines}"},
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✅ *No open action items* from previous meetings."},
        })

    # Suggested points to raise
    suggestions = brief.get("suggested_points", [])
    if suggestions:
        sug_lines = "\n".join(f"• {s}" for s in suggestions[:3])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*💡 Suggested talking points:*\n{sug_lines}"},
        })

    # Attendees
    attendees = mtg_ctx.get("attendees", [])
    if attendees:
        att_text = "  ".join(
            f"*{a['name']}* ({a.get('role', 'member')})" for a in attendees[:5]
        )
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"👥 {att_text}"}],
        })

    # Importance footer + CTA
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"{opt_emoji} *{time_opt}*"},
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open Meeting"},
            "url": f"{settings.FRONTEND_URL}/meetings/{meeting.id}",
            "style": "primary",
        },
    })

    return blocks


async def _run_briefs_cycle() -> dict:
    now = datetime.utcnow()
    window_start = now + timedelta(minutes=BRIEF_WINDOW_MINUTES - 5)   # within 25–35 min window
    window_end   = now + timedelta(minutes=BRIEF_EARLY_MINUTES)

    sent = 0
    skipped = 0

    with SessionLocal() as db:
        upcoming = db.execute(
            select(Meeting).where(
                and_(
                    Meeting.deleted_at.is_(None),
                    Meeting.scheduled_start >= window_start,
                    Meeting.scheduled_start <= window_end,
                    Meeting.status.in_(["scheduled", "pending"]),
                )
            )
        ).scalars().all()

    logger.debug("pre_brief: %d meetings in brief window", len(upcoming))

    for meeting in upcoming:
        with SessionLocal() as db:
            # Re-fetch inside fresh session
            mtg = db.get(Meeting, meeting.id)
            if not mtg:
                continue

            metadata = dict(mtg.meeting_metadata or {})
            already_briefed: list = metadata.get("brief_sent_to", [])

            # Collect attendees
            attendee_ids = list(mtg.attendee_ids or [])
            if mtg.organizer_id and str(mtg.organizer_id) not in [str(x) for x in attendee_ids]:
                attendee_ids.append(str(mtg.organizer_id))
            if mtg.created_by and str(mtg.created_by) not in [str(x) for x in attendee_ids]:
                attendee_ids.append(str(mtg.created_by))

            for uid in attendee_ids:
                uid_str = str(uid)
                if uid_str in already_briefed:
                    skipped += 1
                    continue

                user = db.get(User, uid) if uid else None
                if not user:
                    continue

                ok = await _deliver_brief(mtg, user, db)
                if ok:
                    already_briefed.append(uid_str)
                    sent += 1
                    logger.info(
                        "pre_brief: brief delivered to %s for meeting '%s'",
                        user.email,
                        mtg.title,
                    )

            # Persist which users got the brief
            metadata["brief_sent_to"] = already_briefed
            mtg.meeting_metadata = metadata
            flag_modified(mtg, "meeting_metadata")
            db.commit()

    return {"sent": sent, "skipped": skipped, "meetings_checked": len(upcoming)}


@shared_task(name="send_pre_meeting_briefs", ignore_result=True)
def send_pre_meeting_briefs():
    """
    Runs every 5 minutes via Celery beat.
    Finds meetings starting in the next 25–35 minutes and sends each attendee
    a personalized pre-meeting brief (in-app notification + Slack DM).
    Each user receives at most one brief per meeting.
    """
    if not getattr(settings, "ENABLE_PRE_MEETING_BRIEFS", True):
        logger.debug("pre_brief: disabled via ENABLE_PRE_MEETING_BRIEFS — skipping")
        return

    logger.info("pre_brief: starting brief delivery cycle")
    try:
        result = asyncio.run(_run_briefs_cycle())
        if result["sent"] > 0 or result["meetings_checked"] > 0:
            logger.info(
                "pre_brief: cycle done — sent=%d skipped=%d meetings_checked=%d",
                result["sent"],
                result["skipped"],
                result["meetings_checked"],
            )
    except Exception as exc:
        logger.exception("pre_brief: unhandled task error: %s", exc)

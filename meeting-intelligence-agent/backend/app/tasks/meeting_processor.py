"""
Background Tasks for Meeting Processing
"""
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from celery import shared_task
from app.core.database import SessionLocal
from app.services.ai.transcription import transcription_service
from app.services.ai.nlp import nlp_service
from app.services.integrations.slack import slack_service
from app.services.mentions import detect_and_store_mentions
from app.core.meeting_operations import (
    get_or_create_notification_tracking,
    mark_notification_failed,
    mark_notification_sent,
)
from app.models.meeting import Meeting
from app.models.transcript import Transcript
from app.models.action_item import ActionItem
from app.models.user import User
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _match_user_by_name(users: List[User], owner_name: Optional[str]) -> Optional[User]:
    if not owner_name:
        return None
    target = owner_name.strip().lower()
    if not target:
        return None
    for user in users:
        aliases = {
            str(user.full_name or "").strip().lower(),
            str(user.username or "").strip().lower(),
            str(user.email or "").split("@")[0].strip().lower(),
        }
        first_name = str(user.full_name or "").split()[0].strip().lower() if str(user.full_name or "").strip() else ""
        if first_name:
            aliases.add(first_name)
        if target in aliases:
            return user
    return None


def _parse_due_date(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalize_meeting_id(meeting_id: str | UUID) -> Optional[UUID]:
    if isinstance(meeting_id, UUID):
        return meeting_id
    try:
        return UUID(str(meeting_id))
    except (ValueError, TypeError):
        return None


@shared_task(name="process_meeting_recording")
def process_meeting_recording(meeting_id: str | UUID, recording_path: str):
    """
    Process meeting recording:
    1. Transcribe audio with speaker diarization
    2. Extract action items, mentions, decisions
    3. Generate summary
    4. Send notifications
    """
    import asyncio
    asyncio.run(_process_meeting_async(meeting_id, recording_path))


def process_meeting_recording_background(meeting_id: str | UUID, recording_path: str):
    """FastAPI BackgroundTasks entrypoint for meeting processing"""
    import asyncio
    import threading

    def _runner() -> None:
        asyncio.run(_process_meeting_async(meeting_id, recording_path))

    threading.Thread(target=_runner, daemon=True).start()


async def _process_meeting_async(meeting_id: str | UUID, recording_path: str):  # type: ignore
    """Async implementation of meeting processing"""
    meeting = None
    normalized_meeting_id = _normalize_meeting_id(meeting_id)
    if not normalized_meeting_id:
        logger.error(f"Invalid meeting id provided to processor: {meeting_id}")
        return

    with SessionLocal() as db:
        try:
            # Get meeting
            result = db.execute(
                select(Meeting).where(Meeting.id == normalized_meeting_id)
            )
            meeting = result.scalar_one_or_none()
            
            if not meeting:
                logger.error(f"Meeting {meeting_id} not found")
                return
            
            # Step 1: Transcribe
            logger.info(f"Transcribing meeting {meeting_id}")
            meeting.transcription_status = "processing"  # type: ignore
            db.commit()
            
            transcription = await transcription_service.transcribe_audio(
                recording_path,
                enable_diarization=True,
            )
            
            # Save transcripts
            for idx, segment in enumerate(transcription.segments):
                transcript = Transcript(
                    meeting_id=meeting.id,
                    segment_number=idx,
                    speaker_id=segment.speaker,
                    text=segment.text,
                    start_time=segment.start,
                    end_time=segment.end,
                    duration=segment.end - segment.start,
                    confidence=segment.confidence,
                )
                db.add(transcript)
            
            meeting.transcription_status = "completed"  # type: ignore
            db.commit()
            
            # Step 2: Analyze with NLP
            logger.info(f"Analyzing meeting {meeting_id}")
            meeting.status = "processing"  # type: ignore
            meeting.analysis_status = "processing"  # type: ignore
            db.commit()
            
            full_transcript = "\n".join([s.text for s in transcription.segments])
            
            # Generate summary
            summary = await nlp_service.generate_summary(
                full_transcript,
                meeting.title,  # type: ignore[arg-type]
                meeting.attendee_ids or [],  # type: ignore[arg-type]
            )
            
            meeting.summary = summary.executive_summary  # type: ignore
            meeting.key_decisions = [d.dict() for d in summary.decisions]  # type: ignore
            meeting.discussion_topics = summary.discussion_topics  # type: ignore
            meeting.sentiment_score = summary.sentiment_score  # type: ignore

            candidate_users = db.execute(select(User).where(User.is_active.is_(True))).scalars().all()
            
            # Save action items
            for action_data in summary.action_items:
                owner = _match_user_by_name(candidate_users, action_data.owner)
                action = ActionItem(
                    meeting_id=meeting.id,
                    owner_id=(owner.id if owner else meeting.organizer_id),
                    title=action_data.title,
                    description=action_data.description,
                    priority=action_data.priority,
                    due_date=_parse_due_date(action_data.due_date),
                    confidence_score=action_data.confidence,
                    extraction_method="ai_detected",
                    extracted_from_text=full_transcript[:500],
                    item_metadata={
                        "owner_name": action_data.owner,
                        "due_date_raw": action_data.due_date,
                    },
                )
                db.add(action)

            # Step 2.5: Save personalized mentions for present or absent users
            organizer = db.execute(
                select(User).where(User.id == meeting.organizer_id)
            ).scalar_one_or_none()

            await detect_and_store_mentions(
                db=db,
                meeting=meeting,
                transcript_text=full_transcript,
                candidate_users=candidate_users,
                send_real_time_alerts=True,
                meeting_context={
                    "meeting_summary": summary.executive_summary,
                    "discussion_topics": summary.discussion_topics,
                },
            )
            
            meeting.analysis_status = "completed"  # type: ignore
            meeting.status = "completed"  # type: ignore
            db.commit()

            # Step 3: Send Slack notification (if organizer has Slack connected)
            if organizer:
                slack_creds = (organizer.integrations or {}).get("slack", {})
                if slack_creds.get("bot_token"):
                    channel = slack_creds.get("default_channel", "#general")
                    notification = get_or_create_notification_tracking(
                        db=db,
                        meeting_id=str(meeting.id),
                        notification_type="slack_meeting_summary",
                        provider="slack",
                        recipient=str(channel),
                        payload={
                            "meeting_id": str(meeting.id),
                            "action_count": len(summary.action_items),
                            "decision_count": len(summary.decisions),
                        },
                    )

                    if notification.status == "sent":
                        logger.info(
                            "Skipping duplicate Slack notification for meeting %s (key=%s)",
                            meeting_id,
                            notification.idempotency_key,
                        )
                    else:
                        try:
                            logger.info(f"Sending Slack notification for meeting {meeting_id}")
                            slack_result = await _notify_slack(
                                token=slack_creds["bot_token"],
                                channel=channel,
                                meeting=meeting,
                                action_count=len(summary.action_items),
                                decision_count=len(summary.decisions),
                            )

                            if slack_result.get("ok") is True:
                                mark_notification_sent(
                                    db=db,
                                    idempotency_key=notification.idempotency_key,
                                    response_status="200",
                                    response_body=str(slack_result.get("ts", "ok")),
                                )
                            else:
                                error_message = str(slack_result.get("error", "unknown_slack_error"))
                                mark_notification_failed(
                                    db=db,
                                    idempotency_key=notification.idempotency_key,
                                    error_message=error_message,
                                    response_status="400",
                                )
                                logger.warning(
                                    "Slack notification rejected for meeting %s: %s",
                                    meeting_id,
                                    error_message,
                                )
                        except Exception as slack_err:
                            mark_notification_failed(
                                db=db,
                                idempotency_key=notification.idempotency_key,
                                error_message=str(slack_err),
                                response_status="500",
                            )
                            logger.warning(f"Slack notification failed (non-fatal): {slack_err}")

            # Step 4: Create Linear issues for action items (if organizer has Linear connected)
            if organizer and summary.action_items:
                linear_creds = (organizer.integrations or {}).get("linear", {})
                if linear_creds.get("api_key"):
                    try:
                        logger.info(f"Creating Linear issues for meeting {meeting_id}")
                        await _create_linear_issues(
                            api_key=linear_creds["api_key"],
                            meeting_title=str(meeting.title),
                            action_items=summary.action_items,
                        )
                    except Exception as linear_err:
                        logger.warning(f"Linear sync failed (non-fatal): {linear_err}")

            logger.info(f"Meeting {meeting_id} processing completed successfully")
            
        except Exception as e:
            logger.error(f"Error processing meeting {meeting_id}: {e}", exc_info=True)
            if meeting:
                try:
                    db.rollback()
                except Exception:
                    pass
                try:
                    result = db.execute(select(Meeting).where(Meeting.id == normalized_meeting_id))
                    failed_meeting = result.scalar_one_or_none()
                    if failed_meeting:
                        failed_meeting.transcription_status = "failed"  # type: ignore
                        failed_meeting.analysis_status = "failed"  # type: ignore
                        failed_meeting.status = "failed"  # type: ignore
                        db.commit()
                except Exception as status_error:
                    logger.error(
                        f"Failed to persist failed status for meeting {meeting_id}: {status_error}",
                        exc_info=True,
                    )


@shared_task(name="send_action_reminders")
def send_action_reminders():
    """Send reminders for upcoming action items"""
    import asyncio
    asyncio.run(_send_reminders_async())


async def _send_reminders_async():
    """Send action item reminders"""
    with SessionLocal() as db:
        from datetime import datetime, timedelta
        
        tomorrow = datetime.utcnow() + timedelta(days=1)
        
        # Get action items due tomorrow
        result = db.execute(
            select(ActionItem).where(
                ActionItem.status == "open",
                ActionItem.due_date >= datetime.utcnow(),
                ActionItem.due_date <= tomorrow,
                ActionItem.reminder_sent_24h == False,
            )
        )
        
        items = result.scalars().all()
        
        for item in items:
            try:
                await slack_service.send_action_reminder(
                    user_id=str(item.owner_id),
                    action_item={
                        "id": str(item.id),
                        "title": item.title,
                        "description": item.description,
                        "due_date": item.due_date.strftime("%Y-%m-%d"),
                        "priority": item.priority,
                    },
                )
                item.reminder_sent_24h = True  # type: ignore
                item.reminder_count += 1  # type: ignore
            except Exception as e:
                logger.error(f"Failed to send reminder for action {item.id}: {e}")
        
        db.commit()
        logger.info(f"Sent {len(items)} action item reminders")


# ─── Slack helper ────────────────────────────────────────────────────────────

async def _notify_slack(token: str, channel: str, meeting: "Meeting", action_count: int, decision_count: int):
    """Post a meeting summary card to Slack using the user's bot token."""
    import httpx
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 Meeting Completed: {meeting.title}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": meeting.summary or "_No summary generated._"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*{decision_count}* decisions  •  *{action_count}* action items"},
            ],
        },
    ]
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "blocks": blocks, "text": f"Meeting completed: {meeting.title}"},
        )
        try:
            return response.json()
        except ValueError:
            return {"ok": False, "error": f"non_json_response_{response.status_code}"}


# ─── Linear helper ───────────────────────────────────────────────────────────

async def _create_linear_issues(api_key: str, meeting_title: str, action_items: list):
    """Create Linear issues for each extracted action item."""
    import httpx

    # First, get the first available team id
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"query": "{ teams { nodes { id name } } }"},
        )
    teams = r.json().get("data", {}).get("teams", {}).get("nodes", [])
    if not teams:
        logger.warning("No Linear teams found - skipping issue creation")
        return

    team_id = teams[0]["id"]

    async with httpx.AsyncClient() as client:
        for item in action_items[:10]:  # cap at 10 to avoid flooding
            mutation = """
            mutation CreateIssue($teamId: String!, $title: String!, $description: String) {
              issueCreate(input: { teamId: $teamId, title: $title, description: $description }) {
                success
                issue { id identifier url }
              }
            }
            """
            desc = f"Auto-created from meeting: **{meeting_title}**\n\n{item.description or ''}"
            await client.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": api_key, "Content-Type": "application/json"},
                json={"query": mutation, "variables": {"teamId": team_id, "title": item.title, "description": desc}},
            )
            logger.info(f"Created Linear issue for action: {item.title}")

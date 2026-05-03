"""
Background Tasks for Meeting Processing
"""
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from celery import shared_task
from app.core.database import SessionLocal
from app.core.config import settings
from app.services.ai.transcription import transcription_service
from app.services.ai.nlp import nlp_service
from app.services.integrations.slack import slack_service
from app.services.slack_service import send_slack_dm
from app.services.mentions import detect_and_store_mentions
from app.services.notifications import create_notification
from app.core.meeting_operations import (
    get_or_create_notification_tracking,
    mark_notification_failed,
    mark_notification_sent,
)
from app.models.meeting import Meeting
from app.models.transcript import Transcript
from app.models.action_item import ActionItem
from app.models.mention import Mention
from app.models.user import User
from sqlalchemy import select

logger = logging.getLogger(__name__)

# ── Whisper silence hallucinations ───────────────────────────────────────────
# Whisper emits these phrases when it receives silence instead of speech.
_WHISPER_HALLUCINATIONS: set[str] = {
    "thank you.", "thank you!", "thank you",
    "grazie.", "grazie",
    "you.", "you",
    "bye.", "bye",
    "goodbye.", "goodbye",
    "ok.", "ok", "okay.", "okay",
    "thanks.", "thanks",
    ".", "..", "...",
    "",
}


def _filter_hallucinations(segments):
    """Remove Whisper silence hallucination segments."""
    cleaned = []
    for seg in segments:
        normalized = seg.text.strip().lower().rstrip(".")
        if normalized in {h.rstrip(".") for h in _WHISPER_HALLUCINATIONS}:
            logger.debug(f"Dropping hallucination segment: {repr(seg.text)} @ {seg.start:.1f}s")
            continue
        # Single-word segments under 4 chars are almost always noise
        words = seg.text.strip().split()
        if len(words) == 1 and len(seg.text.strip()) <= 4:
            logger.debug(f"Dropping short noise segment: {repr(seg.text)} @ {seg.start:.1f}s")
            continue
        cleaned.append(seg)
    dropped = len(segments) - len(cleaned)
    if dropped:
        logger.info(f"Hallucination filter: removed {dropped}/{len(segments)} noise segments")
    return cleaned


def _assign_speakers(segments):
    """
    Assign speaker labels using silence-gap heuristic.
    A gap > 1.5s between consecutive segments suggests a speaker change.
    Works best for 2-person calls; produces Speaker 1 / Speaker 2 labels.
    """
    if not segments:
        return
    GAP_THRESHOLD = 1.5  # seconds
    current_speaker = 1
    prev_end = 0.0
    for i, seg in enumerate(segments):
        if i > 0 and (seg.start - prev_end) >= GAP_THRESHOLD:
            current_speaker = 2 if current_speaker == 1 else 1
        seg.speaker = f"Speaker {current_speaker}"
        prev_end = seg.end


def _remap_speaker_names(segments, attendee_names: list[str]) -> None:
    """Replace generic 'Speaker N' labels with real attendee names.

    Maps labels in order of first appearance to attendee_names.
    Any speaker beyond the known attendees becomes 'Guest N'.
    """
    if not segments or not attendee_names:
        return
    seen: dict[str, int] = {}
    for seg in segments:
        if seg.speaker and seg.speaker not in seen:
            seen[seg.speaker] = len(seen)

    label_map: dict[str, str] = {}
    guest_counter = 1
    for label, idx in seen.items():
        if idx < len(attendee_names):
            label_map[label] = attendee_names[idx]
        else:
            label_map[label] = f"Guest {guest_counter}"
            guest_counter += 1

    for seg in segments:
        if seg.speaker in label_map:
            seg.speaker = label_map[seg.speaker]


def _match_user_by_name(users: List[User], owner_name: Optional[str]) -> Optional[User]:
    if not owner_name:
        return None
    target = re.sub(r"[^a-z0-9]+", " ", owner_name.strip().lower()).strip()
    if not target:
        return None
    best_user: Optional[User] = None
    best_score = 0.0
    for user in users:
        aliases = {
            re.sub(r"[^a-z0-9]+", " ", str(user.full_name or "").strip().lower()).strip(),
            re.sub(r"[^a-z0-9]+", " ", str(user.username or "").strip().lower()).strip(),
            re.sub(r"[^a-z0-9]+", " ", str(user.email or "").split("@")[0].strip().lower()).strip(),
        }
        first_name = str(user.full_name or "").split()[0].strip().lower() if str(user.full_name or "").strip() else ""
        if first_name:
            aliases.add(re.sub(r"[^a-z0-9]+", " ", first_name).strip())
        aliases = {alias for alias in aliases if alias}
        if target in aliases:
            return user
        for alias in aliases:
            score = SequenceMatcher(None, target, alias).ratio()
            if target in alias or alias in target:
                score = max(score, 0.92)
            if score > best_score:
                best_score = score
                best_user = user
    return best_user if best_score >= 0.74 else None


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
    """Async implementation of meeting processing.

    Uses two-phase error handling:
    - CRITICAL: transcription, NLP summary, action items → failure = status "failed"
    - NON-CRITICAL: mentions, Slack, Linear → failure is logged, does NOT fail the pipeline
    """
    meeting = None
    normalized_meeting_id = _normalize_meeting_id(meeting_id)
    if not normalized_meeting_id:
        logger.error(f"Invalid meeting id provided to processor: {meeting_id}")
        return

    with SessionLocal() as db:
        # ── Fetch meeting ────────────────────────────────────────────
        try:
            result = db.execute(
                select(Meeting).where(Meeting.id == normalized_meeting_id)
            )
            meeting = result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Failed to fetch meeting {meeting_id}: {e}", exc_info=True)
            return

        if not meeting:
            logger.error(f"Meeting {meeting_id} not found")
            return

        # ═══════════════════════════════════════════════════════════════
        # PHASE 1 — CRITICAL: Transcription + NLP + Action Items
        # If this fails, the meeting is marked as "failed".
        # ═══════════════════════════════════════════════════════════════
        summary = None
        full_transcript = ""
        candidate_users = []

        try:
            # Step 1: Transcribe
            logger.info(f"Transcribing meeting {meeting_id}")
            meeting.transcription_status = "processing"  # type: ignore
            db.commit()

            # Collect attendee display names for speaker mapping
            seen_user_ids: set[str] = set()
            attendee_names: list[str] = []

            def _add_user_name(user_id) -> None:
                uid_str = str(user_id)
                if uid_str in seen_user_ids:
                    return
                seen_user_ids.add(uid_str)
                u = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
                if u:
                    name = str(u.full_name or u.username or u.email or "").strip()
                    if name:
                        attendee_names.append(name)

            # Organizer first (most likely Speaker 1 in recordings)
            if meeting.organizer_id:
                _add_user_name(meeting.organizer_id)
            # Created-by user (bot-joined meetings populate this, not organizer_id)
            if meeting.created_by:
                _add_user_name(meeting.created_by)
            # Remaining attendees
            for uid in list(meeting.attendee_ids or []):
                _add_user_name(uid)

            transcription = await transcription_service.transcribe_audio(
                recording_path,
                enable_diarization=True,
                attendee_names=attendee_names,
            )
            if not transcription.segments:
                raise RuntimeError("Transcript is empty")

            # Filter Whisper silence hallucinations
            transcription.segments = _filter_hallucinations(transcription.segments)
            if not transcription.segments:
                raise RuntimeError("Transcript is empty after hallucination filtering")

            # Only apply gap heuristic if AssemblyAI did NOT handle diarization
            if transcription.diarization_method != "assemblyai":
                _assign_speakers(transcription.segments)
                _remap_speaker_names(transcription.segments, attendee_names)
                logger.info(
                    "Speaker labels assigned via gap heuristic → names: %s",
                    list({s.speaker for s in transcription.segments}),
                )
            else:
                logger.info("Speaker labels from AssemblyAI diarization: %s",
                            list({s.speaker for s in transcription.segments}))

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

            # Generate summary (core extraction)
            summary = await nlp_service.generate_summary(
                full_transcript,
                meeting.title,  # type: ignore[arg-type]
                meeting.attendee_ids or [],  # type: ignore[arg-type]
            )

            meeting.summary = summary.executive_summary  # type: ignore
            meeting.key_decisions = [d.model_dump() for d in summary.decisions]  # type: ignore
            meeting.discussion_topics = summary.discussion_topics  # type: ignore
            meeting.sentiment_score = summary.sentiment_score  # type: ignore

            candidate_users = db.execute(
                select(User).where(
                    User.is_active.is_(True),
                    User.organization_id == meeting.organization_id,
                )
            ).scalars().all()

            # Save action items
            for action_data in summary.action_items:
                if not action_data.title:
                    continue

                # ── Dedup: skip if this task already exists for this meeting ──
                existing_action = db.execute(
                    select(ActionItem).where(
                        ActionItem.meeting_id == meeting.id,
                        ActionItem.title == action_data.title[:500],
                    )
                ).scalar_one_or_none()
                if existing_action:
                    logger.debug(f"Skipping duplicate action item: '{action_data.title}'")
                    continue

                owner = _match_user_by_name(candidate_users, action_data.owner)
                action = ActionItem(
                    organization_id=meeting.organization_id,
                    meeting_id=meeting.id,
                    assigned_to_user_id=(owner.id if owner else meeting.organizer_id),
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
                db.flush()
                if action.assigned_to_user_id:
                    create_notification(
                        db,
                        user_id=action.assigned_to_user_id,
                        organization_id=meeting.organization_id,
                        notification_type="task_assigned",
                        message=f"You were assigned a task from {meeting.title}: {action.title}",
                        notification_metadata={
                            "action_item_id": str(action.id),
                            "meeting_id": str(meeting.id),
                            "source": "ai_processing",
                        },
                    )

            # Save mentions extracted by LLM (from summary.mentions)
            # Maps each name to a real user via fuzzy matching.
            # If no match → user_id = None (NOT organizer fallback).
            logger.info(f"MENTIONS FROM LLM (raw): {[m.user_name for m in summary.mentions]}")
            seen_mention_keys: set = set()
            saved_mention_count = 0
            for mention_data in summary.mentions:
                # mention_data.text = full sentence (always)
                # mention_data.user_name = short name (LLM) OR full sentence (offline path)
                mention_text = (mention_data.text or mention_data.user_name or "").strip()
                if not mention_text:
                    continue

                # Derive the short person name for user-matching and display
                # If user_name looks like a single capitalized word → it IS the name
                # If user_name is a full sentence (offline path) → extract first word
                raw_name = (mention_data.user_name or "").strip()
                if len(raw_name.split()) == 1 and raw_name[0].isupper():
                    person_name = raw_name          # LLM gave us bare "Sara"
                else:
                    # Extract first capitalized word from the sentence
                    m = re.match(r"([A-Z][a-z]+)", mention_text)
                    person_name = m.group(1) if m else raw_name.split()[0]

                # ── Filter: skip if we have NO actionable sentence ────────
                # Require the stored text to be more than a bare name (>1 word)
                if len(mention_text.split()) <= 1:
                    logger.debug(f"Skipping weak mention (single word): '{mention_text}'")
                    continue

                # Use the full sentence as the stored mention text
                mention_name = mention_text

                # ── Deduplicate by normalised sentence ────────────────────
                name_key = re.sub(r"\s+", " ", mention_name.lower())
                if name_key in seen_mention_keys:
                    continue
                seen_mention_keys.add(name_key)

                matched_user = _match_user_by_name(candidate_users, person_name)
                mention_user_id = matched_user.id if matched_user else None


                # ── DB-level duplicate guard ───────────────────────────
                dedup_filters = [
                    Mention.meeting_id == meeting.id,
                    Mention.mentioned_text == mention_name[:1000],
                    Mention.detection_method == "ai_structured_extraction",
                ]
                if mention_user_id is not None:
                    dedup_filters.append(Mention.user_id == mention_user_id)
                else:
                    dedup_filters.append(Mention.user_id.is_(None))
                existing = db.execute(
                    select(Mention).where(*dedup_filters)
                ).scalar_one_or_none()
                if existing:
                    continue

                mention = Mention(
                    organization_id=meeting.organization_id,
                    meeting_id=meeting.id,
                    user_id=mention_user_id,
                    mention_type=mention_data.mention_type or "direct",
                    mentioned_text=mention_name[:1000],
                    full_context=(mention_data.context or "")[:2000],
                    context_before=(mention_data.context or "")[:900] if mention_data.context else None,
                    relevance_score=float(getattr(mention_data, "relevance_score", 80.0)),
                    confidence=float(getattr(mention_data, "confidence", 0.9)),
                    detection_method="ai_structured_extraction",
                    mention_metadata={
                        "extracted_name": mention_name,
                        "matched_to_user": matched_user.full_name if matched_user else None,
                        "source": "llm_summary_extraction",
                    },
                )
                db.add(mention)
                db.flush()
                saved_mention_count += 1

                # ── TASK 3/4: Only notify the matched user ─────────────
                if not matched_user:
                    logger.info(f"Saved mention: '{mention_name}' → no matching user (user_id=None)")
                    continue

                logger.info(
                    f"Saved mention: '{mention_name}' → "
                    f"matched user '{matched_user.full_name}' (id={matched_user.id})"
                )

                # Build a rich notification message
                user_tasks = [
                    a for a in summary.action_items
                    if a.owner in {matched_user.full_name, person_name}
                ]
                message = (
                    f"👋 You were mentioned in a meeting\n\n"
                    f"📌 Meeting: {meeting.title}\n"
                    f"📎 View: {settings.FRONTEND_URL}/meetings/{meeting.id}\n"
                )
                if user_tasks:
                    task = user_tasks[0]
                    message += f"\n📌 Task: {task.title}"
                    if getattr(task, "due_date", None):
                        message += f"\n⏰ Deadline: {task.due_date}"

                notification_meta = {
                    "mention_id": str(mention.id),
                    "meeting_id": str(meeting.id),
                    "mention_type": mention_data.mention_type or "direct",
                    "source": "ai_extraction",
                }

                # ── TASK 2: ALWAYS create in-app (bell) notification ──
                # Then additionally fire Slack DM if the user has it
                # connected.  This means the bell icon is ALWAYS populated
                # regardless of Slack status.
                create_notification(
                    db,
                    user_id=matched_user.id,
                    organization_id=meeting.organization_id,
                    notification_type="mention",
                    message=message,
                    notification_metadata=notification_meta,
                )

                if matched_user.slack_user_id:
                    slack_token = (
                        (matched_user.integrations or {}).get("slack", {}).get("bot_token")
                        or getattr(settings, "SLACK_BOT_TOKEN", None)
                    )
                    send_slack_dm(matched_user.slack_user_id, message, bot_token=slack_token)

            logger.info(f"Saved {saved_mention_count} quality mentions from LLM extraction (raw count: {len(summary.mentions)})")

            # ── CRITICAL PHASE SUCCEEDED → mark completed ────────────
            meeting.analysis_status = "completed"  # type: ignore
            meeting.transcription_status = "completed"  # type: ignore
            meeting.status = "completed"  # type: ignore
            db.commit()
            logger.info(f"Meeting {meeting_id} core processing completed successfully")

        except Exception as e:
            # ── CRITICAL PHASE FAILED → mark as failed ───────────────
            logger.error(f"CRITICAL ERROR processing meeting {meeting_id}: {e}", exc_info=True)
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
                    failed_meeting.meeting_metadata = {
                        **(failed_meeting.meeting_metadata or {}),
                        "last_error": str(e),
                        "failed_at": datetime.utcnow().isoformat(),
                    }  # type: ignore
                    db.commit()
            except Exception as status_error:
                logger.error(
                    f"Failed to persist failed status for meeting {meeting_id}: {status_error}",
                    exc_info=True,
                )
            return  # Stop here — don't run non-critical steps

        # ═══════════════════════════════════════════════════════════════
        # PHASE 2 — NON-CRITICAL: Mentions, Slack, Linear
        # Each step is individually wrapped. Failures are logged but
        # do NOT change meeting status or roll back saved data.
        # ═══════════════════════════════════════════════════════════════

        organizer = None
        try:
            organizer = db.execute(
                select(User).where(User.id == meeting.organizer_id)
            ).scalar_one_or_none()
        except Exception as e:
            logger.warning(f"ERROR fetching organizer for meeting {meeting_id}: {e}")

        # Step 2.5: Mentions extraction (non-critical)
        try:
            await detect_and_store_mentions(
                db=db,
                meeting=meeting,
                transcript_text=full_transcript,
                candidate_users=candidate_users,
                send_real_time_alerts=True,
                meeting_context={
                    "meeting_summary": summary.executive_summary if summary else "",
                    "discussion_topics": summary.discussion_topics if summary else [],
                },
            )
            logger.info(f"Mentions extraction completed for meeting {meeting_id}")
        except Exception as e:
            logger.warning(f"ERROR in mentions extraction for meeting {meeting_id} (non-fatal): {e}", exc_info=True)

        # Step 3: Slack notification (non-critical)
        if organizer and summary:
            slack_creds = (organizer.integrations or {}).get("slack", {})
            if slack_creds.get("bot_token"):
                try:
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
                except Exception as e:
                    logger.warning(f"ERROR in Slack setup for meeting {meeting_id} (non-fatal): {e}")

        # Step 4: Linear issues (non-critical)
        if organizer and summary and summary.action_items:
            linear_creds = (organizer.integrations or {}).get("linear", {})
            if linear_creds.get("api_key"):
                try:
                    logger.info(f"Creating Linear issues for meeting {meeting_id}")
                    await _create_linear_issues(
                        api_key=linear_creds["api_key"],
                        meeting_title=str(meeting.title),
                        action_items=summary.action_items,
                        db=db,
                        meeting_id=meeting.id,
                    )
                except Exception as linear_err:
                    logger.warning(f"Linear sync failed (non-fatal): {linear_err}")

        # Step 5: Email summary via Resend (non-critical)
        if summary and getattr(settings, "RESEND_API_KEY", ""):
            try:
                from app.services.email_service import email_service
                attendee_emails: list[str] = []
                for uid in list(meeting.attendee_ids or []):
                    u = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
                    if u and u.email:
                        attendee_emails.append(str(u.email))
                if organizer and organizer.email and str(organizer.email) not in attendee_emails:
                    attendee_emails.append(str(organizer.email))
                if attendee_emails:
                    await email_service.send_meeting_summary(
                        recipients=attendee_emails,
                        meeting_title=str(meeting.title),
                        meeting_id=str(meeting.id),
                        scheduled_start=meeting.scheduled_start,
                        executive_summary=summary.executive_summary or "",
                        decisions=[d.model_dump() for d in summary.decisions],
                        action_items=[
                            {"title": a.title, "owner": a.owner, "due_date": a.due_date, "priority": a.priority}
                            for a in summary.action_items
                        ],
                    )
                    logger.info(f"Email summary sent to {len(attendee_emails)} attendees for meeting {meeting_id}")
            except Exception as email_err:
                logger.warning(f"Email summary failed (non-fatal): {email_err}")

        # Step 6: Absentee catch-up — in-app bell + Slack + email (non-critical)
        try:
            from app.services.absence_management import absence_management_service
            from app.services.email_service import email_service as _email_svc
            absentees = absence_management_service.find_absentees_for_meeting(db, meeting)
            if absentees:
                logger.info(f"Sending catch-up to {len(absentees)} absentee(s) for meeting {meeting_id}")
            for absentee in absentees:
                try:
                    catch_up = await absence_management_service.generate_catch_up_for_absentee(db, meeting, absentee)
                    if not catch_up:
                        continue

                    actions_assigned = catch_up.get("actions_assigned", [])
                    decisions = catch_up.get("decisions_affecting_work", [])
                    skip_rec = catch_up.get("skip_recommendation", {})

                    # Build plain-text bell message
                    bell_msg = (
                        f"📋 You missed: {meeting.title}\n"
                        f"📅 {meeting.scheduled_start.strftime('%b %d, %Y at %H:%M') if meeting.scheduled_start else ''}\n"
                    )
                    if actions_assigned:
                        titles = ", ".join(a["task"] for a in actions_assigned[:2])
                        bell_msg += f"\n⚡ Action items assigned to you: {titles}"
                    if decisions:
                        bell_msg += f"\n🔑 {len(decisions)} decision(s) may affect your work"
                    if skip_rec.get("recommendation") == "safe_to_skip":
                        bell_msg += "\n✅ Safe to skip future similar meetings"
                    bell_msg += f"\n🔗 {settings.FRONTEND_URL}/meetings/{meeting.id}"

                    # 1. In-app notification (always)
                    create_notification(
                        db,
                        user_id=absentee.id,
                        organization_id=meeting.organization_id,
                        notification_type="meeting_missed",
                        message=bell_msg,
                        notification_metadata={
                            "meeting_id": str(meeting.id),
                            "catch_up": {
                                "actions_count": len(actions_assigned),
                                "decisions_count": len(decisions),
                                "skip_recommendation": skip_rec.get("recommendation"),
                            },
                        },
                    )
                    db.commit()

                    # 2. Slack DM (if connected)
                    try:
                        await absence_management_service.send_catch_up_to_absentee(db, meeting, absentee, catch_up)
                        logger.info(f"Slack catch-up sent to {absentee.email} for meeting {meeting_id}")
                    except Exception as slack_err:
                        logger.warning(f"Slack catch-up failed for {absentee.email} (non-fatal): {slack_err}")

                    # 3. Email (if Resend configured)
                    if absentee.email and getattr(settings, "RESEND_API_KEY", ""):
                        try:
                            await _email_svc.send_absentee_catchup(
                                recipient_email=str(absentee.email),
                                meeting_title=str(meeting.title),
                                meeting_id=str(meeting.id),
                                scheduled_start=meeting.scheduled_start,
                                actions_assigned=actions_assigned,
                                decisions=decisions,
                                skip_recommendation=skip_rec,
                            )
                            logger.info(f"Email catch-up sent to {absentee.email} for meeting {meeting_id}")
                        except Exception as email_err:
                            logger.warning(f"Email catch-up failed for {absentee.email} (non-fatal): {email_err}")

                except Exception as absentee_err:
                    logger.warning(f"Catch-up failed for {getattr(absentee, 'email', '?')} (non-fatal): {absentee_err}")
        except Exception as e:
            logger.warning(f"Absentee catch-up step failed (non-fatal): {e}")

        logger.info(f"Meeting {meeting_id} processing pipeline finished")


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

async def _create_linear_issues(api_key: str, meeting_title: str, action_items: list, db=None, meeting_id=None):
    """Create Linear issues for each extracted action item and save URLs back to DB."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
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
    mutation = """
    mutation CreateIssue($teamId: String!, $title: String!, $description: String) {
      issueCreate(input: { teamId: $teamId, title: $title, description: $description }) {
        success
        issue { id identifier url }
      }
    }
    """

    async with httpx.AsyncClient(timeout=15.0) as client:
        for item in action_items[:10]:  # cap at 10 to avoid flooding
            desc = f"Auto-created from meeting: **{meeting_title}**\n\n{item.description or ''}"
            resp = await client.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": api_key, "Content-Type": "application/json"},
                json={"query": mutation, "variables": {"teamId": team_id, "title": item.title, "description": desc}},
            )
            issue_data = resp.json().get("data", {}).get("issueCreate", {}).get("issue") or {}
            issue_url = issue_data.get("url")
            issue_id = issue_data.get("identifier")
            logger.info(f"Created Linear issue for action: {item.title} → {issue_url}")

            # Save the Linear URL back to the ActionItem row
            if db is not None and meeting_id is not None and (issue_url or issue_id):
                try:
                    db_item = db.execute(
                        select(ActionItem).where(
                            ActionItem.meeting_id == meeting_id,
                            ActionItem.title == item.title[:500],
                        )
                    ).scalar_one_or_none()
                    if db_item:
                        db_item.external_task_id = issue_id  # type: ignore
                        db_item.external_task_url = issue_url  # type: ignore
                        db_item.integration_type = "linear"  # type: ignore
                        db_item.sync_status = "synced"  # type: ignore
                except Exception as db_err:
                    logger.warning(f"Could not save Linear URL to ActionItem: {db_err}")

    if db is not None:
        try:
            db.commit()
        except Exception:
            pass

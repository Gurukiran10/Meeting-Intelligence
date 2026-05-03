"""
Auto-join Celery task.

This is the production replacement for spawning bot sessions as raw asyncio.Tasks
inside the FastAPI process. Running bots here gives you:
  - Crash recovery: acks_late=True re-queues the task if the worker dies mid-session.
  - Retry on host-absent: bot exits early → task re-queues itself after 30 s wait.
  - Dedup: Redis lock ensures only one bot per meeting at a time.
  - Isolation: bot crashes can't affect the HTTP API process.
  - Observability: Flower / Celery events show every bot session.

Retry state machine
───────────────────
attempt 1..MAX_HOST_WAIT_RETRIES
  → bot joins and is immediately alone (host not started yet)
  → recording discarded, meeting set to waiting_for_host
  → task re-queues with countdown=HOST_WAIT_RETRY_DELAY_S

attempt > MAX_HOST_WAIT_RETRIES
  → give up: meeting set to host_absent

On any other error (nav failure, selector crash, etc.) the built-in
MAX_RETRIES=2 loop inside _run_session() handles those retries internally.
After that, the task sets meeting to failed.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from celery import shared_task

from app.core.database import SessionLocal
from app.models.meeting import Meeting

logger = logging.getLogger(__name__)

MAX_HOST_WAIT_RETRIES   = 5     # how many times to retry if host hasn't started
HOST_WAIT_RETRY_DELAY_S = 30    # seconds to wait between host-wait retries
MIN_SESSION_SECONDS     = 30    # recordings shorter than this = bot was alone, not a real meeting


# ── DB helpers (sync, called from executor) ───────────────────────────────────

def _db_set_status(meeting_id: str, status: str, **kwargs) -> None:
    """Update meeting status and any extra fields synchronously."""
    with SessionLocal() as db:
        m = db.get(Meeting, meeting_id)
        if not m:
            return
        m.status = status
        for k, v in kwargs.items():
            if hasattr(m, k):
                setattr(m, k, v)
        db.commit()


def _db_get_meeting(meeting_id: str):
    with SessionLocal() as db:
        return db.get(Meeting, meeting_id)


# ── Core bot runner with host-wait retry ─────────────────────────────────────

async def _run_bot_with_host_retry(
    meeting_id: str,
    meet_url: str,
    user_id: str,
    organization_id: str,
    platform: str = "google_meet",
    bot_display_name: str = "SyncMinds Bot",
    stay_duration_seconds: int = 600,
    recordings_dir: str = "recordings",
    attempt: int = 1,
) -> str:
    """
    Run one bot session. Returns one of:
      "done"        — meeting completed normally
      "retry"       — host wasn't there, should retry after delay
      "host_absent" — gave up after max retries
      "rejected"    — host explicitly denied bot admission
      "failed"      — unrecoverable error
      "cancelled"   — manually stopped
    """
    from app.services.bot_state import BotStatus, set_rich_state, increment_retry

    next_retry_iso = (
        datetime.now(timezone.utc) + timedelta(seconds=HOST_WAIT_RETRY_DELAY_S)
    ).isoformat() if attempt < MAX_HOST_WAIT_RETRIES else None

    logger.info(
        "[AutoJoin] attempt=%d/%d  meeting=%s  platform=%s  url=%s",
        attempt, MAX_HOST_WAIT_RETRIES, meeting_id, platform, meet_url,
    )

    await set_rich_state(
        meeting_id, BotStatus.LAUNCHING,
        user_id=user_id,
        platform=platform,
        meet_url=meet_url,
        attempt=attempt,
        max_attempts=MAX_HOST_WAIT_RETRIES,
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _db_set_status, meeting_id, "joining")

    session_start = datetime.now(timezone.utc)

    try:
        if platform == "zoom":
            from app.services.zoom_bot import join_zoom_meeting
            await join_zoom_meeting(
                zoom_url=meet_url,
                user_id=user_id,
                organization_id=organization_id,
                meeting_id=meeting_id,
                bot_display_name=bot_display_name,
                stay_duration_seconds=stay_duration_seconds,
                recordings_dir=recordings_dir,
            )
        else:
            from app.services.meet_bot import _run_session as _run_meet_session
            await _run_meet_session(
                meet_url=meet_url,
                user_id=user_id,
                organization_id=organization_id,
                meeting_id=meeting_id,
                bot_display_name=bot_display_name,
                stay_duration_seconds=stay_duration_seconds,
                recordings_dir=recordings_dir,
            )

    except asyncio.CancelledError:
        logger.info("[AutoJoin] bot session cancelled  meeting=%s", meeting_id)
        await set_rich_state(meeting_id, BotStatus.CANCELLED)
        return "cancelled"

    except Exception as exc:
        err_str = str(exc)
        logger.error("[AutoJoin] bot session raised  meeting=%s: %s", meeting_id, exc, exc_info=True)
        await set_rich_state(meeting_id, BotStatus.FAILED, last_error=err_str)
        await loop.run_in_executor(None, _db_set_status, meeting_id, "failed")
        return "failed"

    # ── Post-session analysis ─────────────────────────────────────────────────
    session_elapsed_s = (datetime.now(timezone.utc) - session_start).total_seconds()

    meeting = await loop.run_in_executor(None, _db_get_meeting, meeting_id)
    final_db_status = meeting.status if meeting else "unknown"

    # ── Rejection detection ───────────────────────────────────────────────────
    # Bot was in waiting_admission state (waiting room) but _wait_in_meeting timed out.
    # This happens when the host explicitly clicks "Deny" or meeting requires approval.
    # The in-memory _active_bots dict records this via status="waiting_admission" at exit.
    bot_was_rejected = False
    if platform != "zoom":
        from app.services.meet_bot import _active_bots
        mem = _active_bots.get(user_id, {})
        bot_was_rejected = (
            mem.get("status") in ("waiting_admission", "error_attempt_1", "error_attempt_2")
            and session_elapsed_s < 90      # very short session = never got in
        )

    if bot_was_rejected:
        logger.warning("[AutoJoin] bot was rejected  meeting=%s", meeting_id)
        await set_rich_state(
            meeting_id, BotStatus.BOT_REJECTED,
            rejection_reason="host_denied_admission",
            session_elapsed_s=session_elapsed_s,
        )
        await loop.run_in_executor(None, _db_set_status, meeting_id, "bot_rejected")
        return "rejected"

    # ── Host-absent detection ─────────────────────────────────────────────────
    # meet_bot.py sets meeting.status = "waiting_for_host" when bot was alone.
    # If the DB already has that status, trust it.
    host_absent_from_db = final_db_status == "waiting_for_host"

    # Belt-and-suspenders: also catch the case where bot exited very fast
    # (< MIN_SESSION_SECONDS) and the DB didn't get updated yet.
    host_absent_from_timing = (
        session_elapsed_s < MIN_SESSION_SECONDS
        and final_db_status in ("joining", "scheduled")
    )

    host_absent = host_absent_from_db or host_absent_from_timing

    if host_absent and attempt < MAX_HOST_WAIT_RETRIES:
        retry_num = await increment_retry(meeting_id)
        next_retry = (
            datetime.now(timezone.utc) + timedelta(seconds=HOST_WAIT_RETRY_DELAY_S)
        ).isoformat()
        logger.info(
            "[AutoJoin] host not present  meeting=%s  elapsed=%.0fs  retry=%d/%d",
            meeting_id, session_elapsed_s, retry_num, MAX_HOST_WAIT_RETRIES,
        )
        await set_rich_state(
            meeting_id, BotStatus.WAITING_FOR_HOST,
            attempt=retry_num,
            max_attempts=MAX_HOST_WAIT_RETRIES,
            next_retry_at=next_retry,
            session_elapsed_s=session_elapsed_s,
        )
        await loop.run_in_executor(None, _db_set_status, meeting_id, "waiting_for_host")
        return "retry"

    if host_absent and attempt >= MAX_HOST_WAIT_RETRIES:
        logger.warning("[AutoJoin] max host-wait retries reached  meeting=%s", meeting_id)
        await set_rich_state(
            meeting_id, BotStatus.HOST_ABSENT,
            attempt=attempt,
            max_attempts=MAX_HOST_WAIT_RETRIES,
        )
        await loop.run_in_executor(None, _db_set_status, meeting_id, "host_absent")
        return "host_absent"

    # Normal completion
    await set_rich_state(
        meeting_id, BotStatus.COMPLETED,
        attempt=attempt,
        session_elapsed_s=session_elapsed_s,
    )
    return "done"


# ── Celery task ───────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="run_bot_session",
    # acks_late=True means if the worker dies, the task goes back to the queue
    acks_late=True,
    reject_on_worker_lost=True,
    # No built-in Celery retries — we handle host-wait retries ourselves below
    max_retries=0,
    time_limit=7200,       # 2h hard limit
    soft_time_limit=7100,
    queue="bots",
)
def run_bot_session_task(
    self,
    meeting_id: str,
    meet_url: str,
    user_id: str,
    organization_id: str,
    platform: str = "google_meet",
    bot_display_name: str = "SyncMinds Bot",
    stay_duration_seconds: int = 600,
    recordings_dir: str = "recordings",
    attempt: int = 1,
):
    """
    Celery task: run one bot session.
    If the host isn't present, re-queues itself after HOST_WAIT_RETRY_DELAY_S seconds.
    """
    from app.services.bot_state import acquire_lock, release_lock

    async def _main():
        # Dedup: if another worker already has this meeting, skip
        if not await acquire_lock(meeting_id):
            logger.info("[AutoJoin] lock held by another worker  meeting=%s — skipping", meeting_id)
            return

        result = "failed"  # default so `finally` never sees an unbound variable
        try:
            result = await _run_bot_with_host_retry(
                meeting_id=meeting_id,
                meet_url=meet_url,
                user_id=user_id,
                organization_id=organization_id,
                platform=platform,
                bot_display_name=bot_display_name,
                stay_duration_seconds=stay_duration_seconds,
                recordings_dir=recordings_dir,
                attempt=attempt,
            )
        finally:
            # Always release on terminal states; keep lock during retry gap
            if result not in ("retry",):
                await release_lock(meeting_id)
            else:
                # Will re-acquire in the next attempt; release now so the
                # countdown gap doesn't hold a stale lock
                await release_lock(meeting_id)

        if result == "retry":
            logger.info(
                "[AutoJoin] re-queuing in %ds  meeting=%s  attempt=%d → %d",
                HOST_WAIT_RETRY_DELAY_S, meeting_id, attempt, attempt + 1,
            )
            run_bot_session_task.apply_async(
                kwargs={
                    "meeting_id":            meeting_id,
                    "meet_url":              meet_url,
                    "user_id":               user_id,
                    "organization_id":       organization_id,
                    "platform":              platform,
                    "bot_display_name":      bot_display_name,
                    "stay_duration_seconds": stay_duration_seconds,
                    "recordings_dir":        recordings_dir,
                    "attempt":               attempt + 1,
                },
                countdown=HOST_WAIT_RETRY_DELAY_S,
                queue="bots",
            )
        elif result == "rejected":
            # Don't retry — if the host rejected us once they probably meant it.
            # The user will see "Bot rejected" in the UI and can manually re-trigger.
            logger.info("[AutoJoin] bot rejected — no retry  meeting=%s", meeting_id)

    asyncio.run(_main())


# ── Scheduler task (runs on Celery Beat every N minutes) ─────────────────────

@shared_task(name="poll_db_and_auto_join")
def poll_db_and_auto_join():
    """
    Celery Beat task: scan DB for meetings starting soon and dispatch bot tasks.
    This is the DB-based scheduler (no Google Calendar needed).
    Runs every MEET_BOT_LEAD_TIME_MINUTES minutes via beat_schedule.
    """
    asyncio.run(_poll_and_dispatch())


async def _poll_and_dispatch():
    from datetime import timedelta
    from sqlalchemy import select
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    from app.models.user import User
    from app.services.bot_state import acquire_lock, get_bot_state, BotStatus

    if not settings.MEET_BOT_AUTO_JOIN_ENABLED:
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = now - timedelta(minutes=settings.MEET_BOT_LEAD_TIME_MINUTES)
    window_end   = now + timedelta(minutes=settings.MEET_BOT_LEAD_TIME_MINUTES + 1)

    with SessionLocal() as db:
        rows = db.execute(
            select(Meeting).where(
                Meeting.deleted_at.is_(None),
                Meeting.status.in_(["scheduled", "waiting_for_host"]),
                Meeting.meeting_url.isnot(None),
                Meeting.scheduled_start >= window_start,
                Meeting.scheduled_start <= window_end,
            )
        ).scalars().all()

    for meeting in rows:
        meeting_id = str(meeting.id)
        meet_url   = str(meeting.meeting_url)
        platform   = str(meeting.platform or "google_meet")
        user_id    = str(meeting.organizer_id)
        org_id     = str(meeting.organization_id or "")

        # Dedup: skip if bot already active for this meeting
        state = await get_bot_state(meeting_id)
        if state and state.get("status") not in (
            None, BotStatus.PENDING, BotStatus.COMPLETED,
            BotStatus.FAILED, BotStatus.HOST_ABSENT, BotStatus.BOT_REJECTED,
        ):
            logger.debug("[AutoJoin] skipping — bot already active  meeting=%s  status=%s",
                         meeting_id, state.get("status"))
            continue

        logger.info("[AutoJoin] dispatching bot  meeting=%s  platform=%s  url=%s",
                    meeting_id, platform, meet_url)

        run_bot_session_task.apply_async(
            kwargs={
                "meeting_id": meeting_id,
                "meet_url":   meet_url,
                "user_id":    user_id,
                "organization_id": org_id,
                "platform":   platform,
                "bot_display_name": settings.MEET_BOT_DISPLAY_NAME,
                "stay_duration_seconds": settings.MEET_BOT_STAY_DURATION_SECONDS,
                "recordings_dir": settings.RECORDINGS_DIR,
                "attempt": 1,
            },
            queue="bots",
        )

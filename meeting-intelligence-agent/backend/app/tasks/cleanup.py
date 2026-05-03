"""
Cleanup tasks — run on Celery Beat.

cleanup_stuck_meetings       every 5 minutes
  Finds meetings stuck in 'joining' or 'waiting_for_host' with no
  recent bot activity (updated_at stale by > threshold) and resets them.
  Prevents meetings from staying "joining" forever if the bot crashed.

renew_google_webhooks        every 6 hours
  Re-registers Google Calendar events.watch channels before they expire (7-day TTL).
  Zoom webhooks are permanent — no renewal needed.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.meeting import Meeting

logger = logging.getLogger(__name__)

# A meeting stuck in 'joining' for longer than this is assumed to have crashed.
JOINING_STUCK_TIMEOUT_MINUTES     = 15

# A meeting stuck in 'waiting_for_host' longer than this gives up automatically.
# This is a safety net — the retry counter inside auto_join.py should catch this
# first (MAX_HOST_WAIT_RETRIES × HOST_WAIT_RETRY_DELAY_S), but DB status can lag.
WAITING_FOR_HOST_TIMEOUT_MINUTES  = 60

# A meeting stuck in 'in_progress' or 'transcribing' — the bot or processing
# pipeline crashed mid-session without updating the DB status.
IN_PROGRESS_STUCK_TIMEOUT_MINUTES = 30


@shared_task(name="cleanup_stuck_meetings")
def cleanup_stuck_meetings():
    """
    Scan for meetings with stale bot states and reset them.

    Cases handled:
    1. status='joining' + updated_at older than JOINING_STUCK_TIMEOUT_MINUTES
       → bot crashed without cleanup → reset to 'failed'

    2. status='waiting_for_host' + updated_at older than WAITING_FOR_HOST_TIMEOUT_MINUTES
       → host never showed, retries exhausted or bot died → reset to 'host_absent'

    3. status='in_progress' or 'transcribing' + updated_at older than IN_PROGRESS_STUCK_TIMEOUT_MINUTES
       → bot or processing pipeline crashed mid-session → reset to 'failed'

    4. Redis state says 'joining'/'launching' but the lock is gone
       → orphaned Redis state → clear it
    """
    asyncio.run(_do_cleanup())


async def _do_cleanup():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    joining_cutoff      = now - timedelta(minutes=JOINING_STUCK_TIMEOUT_MINUTES)
    host_wait_cutoff    = now - timedelta(minutes=WAITING_FOR_HOST_TIMEOUT_MINUTES)
    in_progress_cutoff  = now - timedelta(minutes=IN_PROGRESS_STUCK_TIMEOUT_MINUTES)

    stuck_joining      = []
    stuck_host_wait    = []
    stuck_in_progress  = []

    with SessionLocal() as db:
        # Meetings stuck in 'joining'
        rows = db.execute(
            select(Meeting).where(
                Meeting.deleted_at.is_(None),
                Meeting.status == "joining",
                Meeting.updated_at < joining_cutoff,
            )
        ).scalars().all()
        stuck_joining = [(str(m.id), str(m.meeting_url or "")) for m in rows]

        # Meetings stuck in 'waiting_for_host'
        rows2 = db.execute(
            select(Meeting).where(
                Meeting.deleted_at.is_(None),
                Meeting.status == "waiting_for_host",
                Meeting.updated_at < host_wait_cutoff,
            )
        ).scalars().all()
        stuck_host_wait = [(str(m.id), str(m.meeting_url or "")) for m in rows2]

        # Meetings stuck in 'in_progress' or 'transcribing'
        rows3 = db.execute(
            select(Meeting).where(
                Meeting.deleted_at.is_(None),
                Meeting.status.in_(["in_progress", "transcribing", "processing"]),
                Meeting.updated_at < in_progress_cutoff,
            )
        ).scalars().all()
        stuck_in_progress = [(str(m.id), str(m.meeting_url or "")) for m in rows3]

    # ── Fix 'joining' stuck ────────────────────────────────────────────────
    for mid, url in stuck_joining:
        logger.warning("[Cleanup] stuck in 'joining'  meeting=%s  resetting to failed", mid)
        await _reset_meeting(mid, "failed", "bot_crashed_while_joining")

    # ── Fix 'waiting_for_host' timeout ────────────────────────────────────
    for mid, url in stuck_host_wait:
        logger.warning("[Cleanup] stuck in 'waiting_for_host'  meeting=%s  resetting to host_absent", mid)
        await _reset_meeting(mid, "host_absent", "host_absent_timeout")

    # ── Fix 'in_progress'/'transcribing' stuck ─────────────────────────────
    for mid, url in stuck_in_progress:
        logger.warning("[Cleanup] stuck in progress/transcribing  meeting=%s  resetting to failed", mid)
        await _reset_meeting(mid, "failed", "bot_or_pipeline_crashed_mid_session")

    # ── Clean orphaned Redis state ─────────────────────────────────────────
    await _clean_orphaned_redis_state()

    total = len(stuck_joining) + len(stuck_host_wait) + len(stuck_in_progress)
    if total:
        logger.info("[Cleanup] fixed %d stuck meeting(s)", total)
    else:
        logger.debug("[Cleanup] no stuck meetings found")


async def _reset_meeting(meeting_id: str, new_status: str, reason: str) -> None:
    """Update DB status and Redis state for a stuck meeting."""
    from app.services.bot_state import set_bot_state, release_lock, BotStatus

    loop = asyncio.get_event_loop()

    def _db_update():
        with SessionLocal() as db:
            m = db.get(Meeting, meeting_id)
            if m and m.status not in ("completed", "transcribing", "analyzing"):
                m.status = new_status
                m.meeting_metadata = {
                    **(m.meeting_metadata or {}),
                    "cleanup_reason": reason,
                    "cleaned_at": datetime.utcnow().isoformat(),
                }
                db.commit()

    await loop.run_in_executor(None, _db_update)

    status_map = {
        "failed":       BotStatus.FAILED,
        "host_absent":  BotStatus.HOST_ABSENT,
    }
    await set_bot_state(
        meeting_id,
        status_map.get(new_status, BotStatus.FAILED),
        cleanup_reason=reason,
    )
    await release_lock(meeting_id)


async def _clean_orphaned_redis_state() -> None:
    """
    Find Redis bot:state keys whose lock has already been released
    but state still shows an 'active' status. Clear them.
    """
    from app.core.redis import redis_client
    from app.services.bot_state import BotStatus

    if not redis_client:
        return

    active_statuses = {
        BotStatus.LAUNCHING, BotStatus.NAVIGATING, BotStatus.PRE_JOIN,
        BotStatus.JOINING, BotStatus.WAITING_ADMISSION, BotStatus.IN_MEETING,
        BotStatus.RECORDING, BotStatus.STOPPING,
    }

    keys = await redis_client.keys("bot:state:*")
    for key in keys:
        try:
            state = await redis_client.hgetall(key)
            if not state:
                continue
            if state.get("status") not in active_statuses:
                continue
            # If the lock is gone but state is still active → orphaned
            meeting_id = state.get("meeting_id", key.split(":")[-1])
            lock_exists = await redis_client.exists(f"bot:lock:{meeting_id}")
            if not lock_exists:
                logger.info("[Cleanup] orphaned Redis state  meeting=%s  status=%s", meeting_id, state.get("status"))
                await redis_client.hset(key, "status", BotStatus.FAILED)
                await redis_client.hset(key, "cleanup_reason", "orphaned_state")
        except Exception as exc:
            logger.debug("[Cleanup] orphan check error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Google Calendar webhook renewal
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(name="renew_google_webhooks")
def renew_google_webhooks():
    """
    Re-register Google Calendar events.watch channels expiring within 24 hours.
    Google webhook channels expire after ≤ 7 days and silently stop sending.
    This Beat task ensures continuous coverage.
    """
    asyncio.run(_do_renew())


async def _do_renew():
    from app.core.database import SessionLocal
    from app.api.v1.endpoints.webhooks import renew_expiring_google_webhooks

    with SessionLocal() as db:
        count = await renew_expiring_google_webhooks(db)
    logger.info("[WebhookRenewal] renewed %d Google Calendar channel(s)", count)

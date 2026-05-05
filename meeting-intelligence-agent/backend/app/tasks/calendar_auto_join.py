"""
Celery beat task: polls Google Calendar every 5 minutes and triggers
the Meet bot for meetings starting within the next 2 minutes.
"""
import asyncio
import copy
import logging
from datetime import datetime, timedelta, timezone

import httpx
from celery import shared_task
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User

logger = logging.getLogger(__name__)


async def _get_valid_google_token(user: User, db) -> str | None:
    """Return a valid Google access token, refreshing if necessary. Returns None if not possible."""
    from sqlalchemy.orm.attributes import flag_modified

    google = dict((user.integrations or {}).get("google", {}))
    access_token = google.get("access_token") or user.google_access_token
    refresh_token = (
        google.get("refresh_token")
        or google.get("oauth_refresh_token")
        or user.google_refresh_token
    )

    if not refresh_token:
        return None

    # Check if current token still has >60s left
    expires_at_raw = google.get("token_expires_at")
    if expires_at_raw:
        try:
            from dateutil import parser as dtparser
            expires_at = dtparser.parse(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
                return access_token
        except Exception:
            pass
    elif access_token and user.google_token_expiry:
        if user.google_token_expiry > datetime.utcnow() + timedelta(seconds=60):
            return access_token

    # Refresh
    client_id = (google.get("client_id") or settings.GOOGLE_CLIENT_ID or "").strip()
    client_secret = (google.get("client_secret") or settings.GOOGLE_CLIENT_SECRET or "").strip()
    if not client_id or not client_secret:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        if resp.status_code != 200:
            logger.warning("auto_join: token refresh failed for user %s: %s", user.id, resp.text[:200])
            return None

        data = resp.json()
        new_token = data.get("access_token")
        if not new_token:
            return None

        expires_in = data.get("expires_in", 3600)
        integrations = copy.deepcopy(user.integrations or {})
        g = dict(integrations.get("google", {}))
        g["access_token"] = new_token
        g["token_expires_at"] = (
            datetime.utcnow() + timedelta(seconds=int(expires_in) - 30)
        ).replace(microsecond=0).isoformat()
        integrations["google"] = g
        user.integrations = integrations
        flag_modified(user, "integrations")
        db.commit()
        db.refresh(user)
        return new_token

    except Exception as exc:
        logger.warning("auto_join: token refresh exception for user %s: %s", user.id, exc)
        return None


async def _run_for_all_users() -> list[dict]:
    """Fetch upcoming calendar events and dispatch bot sessions via Celery bots queue.

    Previous implementation called join_google_meet() which spawned the bot
    as an asyncio.create_task inside this worker.  When asyncio.run() finished,
    the event loop closed and the bot was silently killed mid-session.

    Now we:
      1. Fetch calendar events to find meetings starting soon.
      2. Upsert a Meeting record in the DB (so the meeting exists).
      3. Dispatch run_bot_session_task.apply_async(queue="bots") — same as
         poll_db_and_auto_join, ensuring the bot runs on the dedicated worker.
    """
    from app.services.meet_bot import is_valid_meet_url
    from app.tasks.auto_join import run_bot_session_task
    from app.models.meeting import Meeting
    from dateutil import parser as dtparser

    results = []

    with SessionLocal() as db:
        all_users = db.execute(select(User)).scalars().all()
        google_users = [
            u for u in all_users
            if (
                (u.integrations or {}).get("google", {}).get("refresh_token")
                or (u.integrations or {}).get("google", {}).get("oauth_refresh_token")
                or u.google_refresh_token
            )
        ]

    logger.debug("auto_join: %d Google-connected users to check", len(google_users))

    for user in google_users:
        with SessionLocal() as db:
            user_obj = db.get(User, user.id)
            if not user_obj:
                continue
            try:
                access_token = await _get_valid_google_token(user_obj, db)
                if not access_token:
                    logger.debug("auto_join: no valid token for user %s — skipping", user_obj.id)
                    continue

                user_id = str(user_obj.id)
                org_id = str(user_obj.organization_id)

                # ── Fetch calendar events ────────────────────────────────
                now_utc = datetime.now(timezone.utc)
                lead_time = 5  # minutes — wider window to reliably catch meetings
                time_min = (now_utc - timedelta(minutes=lead_time)).isoformat().replace("+00:00", "Z")
                time_max = (now_utc + timedelta(minutes=lead_time + 1)).isoformat().replace("+00:00", "Z")

                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params={
                            "singleEvents": "true",
                            "orderBy": "startTime",
                            "maxResults": 20,
                            "timeMin": time_min,
                            "timeMax": time_max,
                        },
                    )

                if resp.status_code != 200:
                    logger.warning("auto_join: calendar fetch failed for user %s: %s", user_id, resp.text[:200])
                    results.append({"user_id": user_id, "error": resp.text[:200]})
                    continue

                triggered = 0
                for event in resp.json().get("items", []):
                    meet_url = event.get("hangoutLink")
                    if not meet_url:
                        for ep in (event.get("conferenceData") or {}).get("entryPoints", []):
                            if ep.get("entryPointType") == "video" and "meet.google.com" in str(ep.get("uri", "")):
                                meet_url = ep["uri"]
                                break
                    if meet_url:
                        meet_url = meet_url.split("?")[0].split("#")[0].rstrip("/")

                    if not meet_url or not is_valid_meet_url(meet_url):
                        continue

                    start_str = (event.get("start") or {}).get("dateTime")
                    if not start_str:
                        continue

                    start_dt = dtparser.parse(start_str)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)

                    delta_seconds = (start_dt - now_utc).total_seconds()
                    if not (-(lead_time * 60) <= delta_seconds <= lead_time * 60):
                        continue

                    # ── Ensure a Meeting record exists in DB ──────────────
                    meeting_id = None
                    with SessionLocal() as mdb:
                        existing = mdb.execute(
                            select(Meeting).where(
                                Meeting.meeting_url == meet_url,
                                Meeting.deleted_at.is_(None),
                            )
                        ).scalar_one_or_none()

                        if existing:
                            meeting_id = str(existing.id)
                            # Only dispatch if it's in a joinable state
                            if existing.status not in ("scheduled", "waiting_for_host"):
                                logger.debug(
                                    "auto_join: skipping %s — status=%s",
                                    meeting_id, existing.status,
                                )
                                continue
                        else:
                            # Create a new meeting record
                            new_meeting = Meeting(
                                organization_id=org_id,
                                title=event.get("summary", "Google Meet"),
                                platform="google_meet",
                                meeting_url=meet_url,
                                scheduled_start=start_dt.replace(tzinfo=None),
                                scheduled_end=(start_dt + timedelta(hours=1)).replace(tzinfo=None),
                                organizer_id=user_id,
                                created_by=user_id,
                                status="scheduled",
                                transcription_status="pending",
                            )
                            mdb.add(new_meeting)
                            mdb.commit()
                            mdb.refresh(new_meeting)
                            meeting_id = str(new_meeting.id)

                    # ── Check bot_state to avoid duplicate dispatch ────────
                    from app.services.bot_state import get_bot_state, BotStatus
                    state = await get_bot_state(meeting_id)
                    if state and state.get("status") not in (
                        None, "", BotStatus.PENDING, BotStatus.COMPLETED,
                        BotStatus.FAILED, BotStatus.HOST_ABSENT, BotStatus.BOT_REJECTED,
                    ):
                        logger.debug(
                            "auto_join: skipping %s — bot already active (state=%s)",
                            meeting_id, state.get("status"),
                        )
                        continue

                    # ── Dispatch to bots queue ────────────────────────────
                    logger.info(
                        "auto_join: dispatching bot via Celery  meeting=%s  url=%s",
                        meeting_id, meet_url,
                    )
                    run_bot_session_task.apply_async(
                        kwargs={
                            "meeting_id": meeting_id,
                            "meet_url": meet_url,
                            "user_id": user_id,
                            "organization_id": org_id,
                            "platform": "google_meet",
                            "bot_display_name": getattr(settings, "MEET_BOT_DISPLAY_NAME", "SyncMinds Bot"),
                            "stay_duration_seconds": getattr(settings, "MEET_BOT_STAY_DURATION_SECONDS", 600),
                            "recordings_dir": getattr(settings, "RECORDINGS_DIR", "recordings"),
                            "attempt": 1,
                        },
                        queue="bots",
                    )
                    triggered += 1

                if triggered > 0:
                    logger.info("auto_join: triggered %d bot(s) for user %s", triggered, user_id)
                results.append({"user_id": user_id, "triggered": triggered})

            except Exception as exc:
                logger.exception("auto_join: error processing user %s: %s", user_obj.id, exc)
                results.append({"user_id": str(user_obj.id), "error": str(exc)})

    return results


@shared_task(name="poll_calendar_and_auto_join", ignore_result=True)
def poll_calendar_and_auto_join():
    """
    Runs every 5 minutes via Celery beat.
    Polls Google Calendar for all connected users and triggers the Meet bot
    for any meeting starting within the next 2 minutes.
    """
    if not getattr(settings, "MEET_BOT_AUTO_JOIN_ENABLED", True):
        logger.debug("auto_join: disabled via MEET_BOT_AUTO_JOIN_ENABLED — skipping")
        return

    logger.info("auto_join: starting calendar poll cycle")
    try:
        results = asyncio.run(_run_for_all_users())
        triggered_total = sum(r.get("triggered", 0) for r in results if isinstance(r, dict))
        logger.info(
            "auto_join: cycle complete — checked %d users, triggered %d bot(s)",
            len(results),
            triggered_total,
        )
    except Exception as exc:
        logger.exception("auto_join: unhandled task error: %s", exc)

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
    from app.services.meet_bot import auto_join_upcoming_meets

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

                result = await auto_join_upcoming_meets(
                    user_id=str(user_obj.id),
                    organization_id=str(user_obj.organization_id),
                    access_token=access_token,
                    lead_time_minutes=2,
                )

                if result.get("triggered", 0) > 0:
                    logger.info(
                        "auto_join: triggered %d bot(s) for user %s: %s",
                        result["triggered"],
                        user_obj.id,
                        result.get("meetings"),
                    )
                results.append({"user_id": str(user_obj.id), **result})

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

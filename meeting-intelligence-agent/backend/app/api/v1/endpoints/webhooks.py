"""
Webhook handlers — event-driven bot triggering.

Google Calendar (events.watch)
───────────────────────────────
POST /api/v1/webhooks/google-calendar/register
  → Calls events.watch, stores channel_id/expiry in user.integrations["google"]
  → Expires every 7 days → Celery Beat re-registers automatically

POST /api/v1/webhooks/google-calendar/{user_id}
  → Google fires this on ANY calendar change
  → We fetch upcoming events, filter to ones in our DB, dispatch bot

Zoom
─────
POST /api/v1/webhooks/zoom
  → Handles url_validation challenge (Zoom handshake)
  → On meeting.started → dispatch bot for matching meeting in DB
  → On meeting.ended   → release bot lock
  → Signature verified with HMAC-SHA256 using ZOOM_WEBHOOK_SECRET

Why this is better than polling:
  Polling delay:   1–5 minutes
  Webhook delay:   < 3 seconds
"""
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user, require_org_member
from app.core.config import settings
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_bot(meeting: Meeting) -> None:
    """Enqueue a bot session for a meeting. Dedup is handled inside the task."""
    mid      = str(meeting.id)
    url      = str(meeting.meeting_url)
    platform = str(meeting.platform or "google_meet")
    uid      = str(meeting.organizer_id)
    org_id   = str(meeting.organization_id or "")

    try:
        from app.tasks.auto_join import run_bot_session_task
        run_bot_session_task.apply_async(
            kwargs={
                "meeting_id":            mid,
                "meet_url":              url,
                "user_id":               uid,
                "organization_id":       org_id,
                "platform":              platform,
                "bot_display_name":      settings.MEET_BOT_DISPLAY_NAME,
                "stay_duration_seconds": settings.MEET_BOT_STAY_DURATION_SECONDS,
                "recordings_dir":        settings.RECORDINGS_DIR,
                "attempt":               1,
            },
            queue="bots",
        )
        logger.info("[Webhook] dispatched bot  meeting=%s  platform=%s", mid, platform)
    except Exception as e:
        logger.error("[Webhook] Celery dispatch failed  meeting=%s: %s", mid, e)


def _find_meeting_by_url(db: Session, url: str, platform: Optional[str] = None):
    """Find a non-deleted scheduled/waiting_for_host meeting by its URL."""
    url_clean = url.split("?")[0].split("#")[0].rstrip("/")
    q = select(Meeting).where(
        Meeting.deleted_at.is_(None),
        Meeting.meeting_url.ilike(f"%{url_clean}%"),
        Meeting.status.in_(["scheduled", "waiting_for_host"]),
    )
    if platform:
        q = q.where(Meeting.platform == platform)
    return db.execute(q).scalar_one_or_none()


def _find_meeting_by_external_id(db: Session, external_id: str):
    return db.execute(
        select(Meeting).where(
            Meeting.deleted_at.is_(None),
            Meeting.external_id == external_id,
            Meeting.status.in_(["scheduled", "waiting_for_host"]),
        )
    ).scalar_one_or_none()


def _is_starting_soon(start_dt: datetime, lead_minutes: int) -> bool:
    """True if meeting starts within [-lead, +lead+1] minutes of now."""
    now = datetime.now(timezone.utc)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    delta = (start_dt - now).total_seconds()
    return -(lead_minutes * 60) <= delta <= (lead_minutes + 1) * 60


# ─────────────────────────────────────────────────────────────────────────────
# Google Calendar webhooks
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/google-calendar/register", summary="Register Google Calendar push notifications")
async def register_google_calendar_webhook(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """
    Registers a Google Calendar events.watch channel for the current user.
    Google will POST to /api/v1/webhooks/google-calendar/{user_id} whenever
    any calendar event changes.

    Channel expires after 7 days. Call this again (or let the renewal Beat
    job handle it) before expiry — Google silently stops sending after expiry.
    """
    from app.api.v1.endpoints.integrations import (
        _get_google_access_token,
        _get_integration,
        _save_integration,
    )

    access_token = await _get_google_access_token(db=db, current_user=current_user)

    channel_id = str(uuid.uuid4())
    channel_token = str(uuid.uuid4())    # We verify this in the handler
    webhook_url = f"{settings.FRONTEND_URL.rstrip('/')}/api/v1/webhooks/google-calendar/{current_user.id}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events/watch",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "id":      channel_id,
                "type":    "web_hook",
                "address": webhook_url,
                "token":   channel_token,
                "params":  {"ttl": "604800"},   # 7 days in seconds
            },
        )

    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Calendar watch API error {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()
    expiry_ms = int(data.get("expiration", 0))
    expiry_iso = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).isoformat()

    google = _get_integration(current_user, "google")
    google["webhook_channel_id"]    = channel_id
    google["webhook_resource_id"]   = data.get("resourceId", "")
    google["webhook_channel_token"] = channel_token
    google["webhook_expiry"]        = expiry_iso
    _save_integration(db, current_user, "google", google)

    logger.info(
        "[GoogleWebhook] registered  user=%s  channel=%s  expires=%s",
        current_user.id, channel_id, expiry_iso,
    )
    return {
        "registered":  True,
        "channel_id":  channel_id,
        "expires_at":  expiry_iso,
        "webhook_url": webhook_url,
    }


@router.post("/google-calendar/{user_id}", include_in_schema=False)
async def google_calendar_webhook(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_goog_channel_id: Optional[str]    = Header(None, alias="X-Goog-Channel-ID"),
    x_goog_channel_token: Optional[str] = Header(None, alias="X-Goog-Channel-Token"),
    x_goog_resource_state: Optional[str]= Header(None, alias="X-Goog-Resource-State"),
):
    """
    Google Calendar push notification endpoint.

    Called by Google when any event in the user's primary calendar changes.
    We ignore 'sync' (initial handshake) and only react to 'exists' (event changed).
    On 'exists', we fetch upcoming events and trigger the bot for meetings
    that are starting in the next lead_time_minutes and exist in our DB.
    """
    # Ignore initial sync message
    if x_goog_resource_state == "sync":
        return {"ok": True}

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Verify channel token
    from app.api.v1.endpoints.integrations import _get_integration
    google = _get_integration(user, "google")
    expected_token = google.get("webhook_channel_token", "")
    if expected_token and x_goog_channel_token != expected_token:
        logger.warning("[GoogleWebhook] invalid token  user=%s", user_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid channel token")

    # React in background so Google gets a fast 200 back
    background_tasks.add_task(_handle_google_calendar_event, user, db)
    return {"ok": True}


async def _handle_google_calendar_event(user: User, db: Session) -> None:
    """
    Fetch Google Calendar events starting soon and dispatch bots.
    Called as a background task — must not raise.
    """
    from app.api.v1.endpoints.integrations import _get_google_access_token

    try:
        access_token = await _get_google_access_token(db=db, current_user=user)
    except Exception as exc:
        logger.error("[GoogleWebhook] token refresh failed  user=%s: %s", user.id, exc)
        return

    lead = settings.MEET_BOT_LEAD_TIME_MINUTES
    now_utc = datetime.now(timezone.utc)
    time_min = (now_utc - timedelta(minutes=lead)).isoformat().replace("+00:00", "Z")
    time_max = (now_utc + timedelta(minutes=lead + 1)).isoformat().replace("+00:00", "Z")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "singleEvents": "true",
                    "orderBy":      "startTime",
                    "maxResults":   20,
                    "timeMin":      time_min,
                    "timeMax":      time_max,
                },
            )
        if resp.status_code != 200:
            logger.error("[GoogleWebhook] calendar fetch failed: %s", resp.text[:200])
            return
        events = resp.json().get("items", [])
    except Exception as exc:
        logger.error("[GoogleWebhook] calendar fetch error  user=%s: %s", user.id, exc)
        return

    for event in events:
        meet_url = event.get("hangoutLink")
        if not meet_url:
            for ep in (event.get("conferenceData") or {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video" and "meet.google.com" in str(ep.get("uri", "")):
                    meet_url = ep["uri"]
                    break
        if not meet_url:
            continue
        meet_url = meet_url.split("?")[0].rstrip("/")

        start_str = (event.get("start") or {}).get("dateTime")
        if not start_str:
            continue
        try:
            from dateutil import parser as dtparser
            start_dt = dtparser.parse(start_str)
        except Exception:
            continue

        if not _is_starting_soon(start_dt, lead):
            continue

        # Only trigger for meetings that exist in our DB (user created them via app)
        external_id = event.get("id", "")
        meeting = _find_meeting_by_external_id(db, external_id)
        if not meeting:
            meeting = _find_meeting_by_url(db, meet_url, "google_meet")
        if not meeting:
            logger.debug("[GoogleWebhook] skipping event not in DB  id=%s", external_id)
            continue

        logger.info(
            "[GoogleWebhook] triggering bot  user=%s  meeting=%s  event=%s",
            user.id, meeting.id, external_id,
        )
        _dispatch_bot(meeting)


# ─────────────────────────────────────────────────────────────────────────────
# Zoom webhooks
# ─────────────────────────────────────────────────────────────────────────────

def _verify_zoom_signature(body_bytes: bytes, signature_header: str, timestamp_header: str) -> bool:
    """
    Zoom signs webhook payloads using HMAC-SHA256.
    Signature format:  v0={hex(hmac(SHA256, "v0:{ts}:{body}", ZOOM_WEBHOOK_SECRET))}
    """
    secret = settings.ZOOM_WEBHOOK_SECRET
    if not secret:
        return True     # not configured → skip verification in dev
    message = f"v0:{timestamp_header}:{body_bytes.decode()}"
    expected = "v0=" + hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@router.post("/zoom", summary="Zoom webhook endpoint")
async def zoom_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_zm_signature: Optional[str]          = Header(None, alias="x-zm-signature"),
    x_zm_request_timestamp: Optional[str]  = Header(None, alias="x-zm-request-timestamp"),
):
    """
    Handles Zoom webhook events.

    Required Zoom Marketplace App settings:
      Event subscriptions: meeting.started, meeting.ended, meeting.participant_joined

    URL validation:  Zoom sends endpoint.url_validation when you first save the webhook.
    Respond with:  {"plainToken": ..., "encryptedToken": sha256(secret, plainToken)}
    """
    body_bytes = await request.body()

    # Signature verification
    if x_zm_signature and x_zm_request_timestamp:
        if not _verify_zoom_signature(body_bytes, x_zm_signature, x_zm_request_timestamp):
            logger.warning("[ZoomWebhook] signature verification failed")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    event = payload.get("event")

    # ── URL validation challenge (sent when you save the webhook in Zoom Marketplace)
    if event == "endpoint.url_validation":
        plain_token = payload.get("payload", {}).get("plainToken", "")
        encrypted = hmac.new(
            (settings.ZOOM_WEBHOOK_SECRET or "").encode(),
            plain_token.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {"plainToken": plain_token, "encryptedToken": encrypted}

    # ── meeting.started: host has started the meeting → join immediately
    if event == "meeting.started":
        obj = payload.get("payload", {}).get("object", {})
        background_tasks.add_task(_handle_zoom_meeting_started, obj, db)
        return {"ok": True}

    # ── meeting.ended: release bot lock so retries don't keep firing
    if event == "meeting.ended":
        obj = payload.get("payload", {}).get("object", {})
        background_tasks.add_task(_handle_zoom_meeting_ended, obj, db)
        return {"ok": True}

    return {"ok": True, "ignored": event}


async def _handle_zoom_meeting_started(obj: Dict[str, Any], db: Session) -> None:
    """
    Zoom meeting.started fires the moment the host clicks "Start".
    Find the matching meeting in our DB and dispatch the bot.
    """
    zoom_meeting_id = str(obj.get("id", ""))
    join_url        = str(obj.get("join_url", ""))
    topic           = str(obj.get("topic", "Zoom Meeting"))

    if not zoom_meeting_id:
        return

    meeting = _find_meeting_by_external_id(db, zoom_meeting_id)
    if not meeting:
        meeting = _find_meeting_by_url(db, join_url, "zoom")
    if not meeting:
        logger.debug("[ZoomWebhook] meeting.started — not in DB  zoom_id=%s", zoom_meeting_id)
        return

    logger.info(
        "[ZoomWebhook] meeting.started → dispatching bot  meeting=%s  zoom_id=%s",
        meeting.id, zoom_meeting_id,
    )
    _dispatch_bot(meeting)


async def _handle_zoom_meeting_ended(obj: Dict[str, Any], db: Session) -> None:
    """
    When a Zoom meeting ends, release the Redis bot lock so any pending
    retry doesn't try to join a meeting that no longer exists.
    """
    zoom_meeting_id = str(obj.get("id", ""))
    meeting = _find_meeting_by_external_id(db, zoom_meeting_id)
    if not meeting:
        return

    from app.services.bot_state import release_lock, set_bot_state, BotStatus
    mid = str(meeting.id)
    await set_bot_state(mid, BotStatus.COMPLETED, reason="zoom_meeting_ended")
    await release_lock(mid)
    logger.info("[ZoomWebhook] meeting.ended — lock released  meeting=%s", mid)


# ─────────────────────────────────────────────────────────────────────────────
# Google Calendar webhook renewal (called by Celery Beat)
# ─────────────────────────────────────────────────────────────────────────────

async def renew_expiring_google_webhooks(db: Session) -> int:
    """
    Re-register Google Calendar webhooks that expire within the next 24 hours.
    Called by the `renew_google_webhooks` Celery Beat task.
    Returns count of renewed channels.
    """
    from app.api.v1.endpoints.integrations import _get_integration, _get_google_access_token
    from sqlalchemy import select
    from app.models.user import User as UserModel

    users = db.execute(
        select(UserModel).where(UserModel.google_connected.is_(True))
    ).scalars().all()

    renewed = 0
    for user in users:
        google = _get_integration(user, "google")
        expiry_str = google.get("webhook_expiry")
        if not expiry_str:
            continue
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        # Renew if expiring within 24 hours
        if expiry > datetime.now(timezone.utc) + timedelta(hours=24):
            continue
        try:
            # Stop the old channel
            resource_id = google.get("webhook_resource_id", "")
            channel_id  = google.get("webhook_channel_id", "")
            if resource_id and channel_id:
                access_token = await _get_google_access_token(db=db, current_user=user)
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        "https://www.googleapis.com/calendar/v3/channels/stop",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json={"id": channel_id, "resourceId": resource_id},
                    )
        except Exception:
            pass   # Stop failure is non-fatal; the channel will expire anyway

        # Register a new one — reuse the existing register endpoint logic
        try:
            from fastapi.testclient import TestClient  # noqa — not actually used
            # Inline the registration logic (avoids circular import via Request)
            new_channel_id    = str(uuid.uuid4())
            new_token         = str(uuid.uuid4())
            webhook_url = (
                f"{settings.FRONTEND_URL.rstrip('/')}"
                f"/api/v1/webhooks/google-calendar/{user.id}"
            )
            access_token = await _get_google_access_token(db=db, current_user=user)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events/watch",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "id":      new_channel_id,
                        "type":    "web_hook",
                        "address": webhook_url,
                        "token":   new_token,
                        "params":  {"ttl": "604800"},
                    },
                )
            if resp.status_code in (200, 201):
                data = resp.json()
                expiry_ms  = int(data.get("expiration", 0))
                expiry_iso = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).isoformat()
                from app.api.v1.endpoints.integrations import _save_integration
                google["webhook_channel_id"]    = new_channel_id
                google["webhook_resource_id"]   = data.get("resourceId", "")
                google["webhook_channel_token"] = new_token
                google["webhook_expiry"]        = expiry_iso
                _save_integration(db, user, "google", google)
                renewed += 1
                logger.info("[GoogleWebhook] renewed channel  user=%s  expires=%s", user.id, expiry_iso)
        except Exception as exc:
            logger.error("[GoogleWebhook] renewal failed  user=%s: %s", user.id, exc)

    return renewed

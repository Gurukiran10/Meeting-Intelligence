"""
Bot state management — Redis-backed, meeting-centric.

Key schema:
  bot:lock:{meeting_id}    STRING  NX EX 7200   dedup — only one bot per meeting
  bot:state:{meeting_id}   HASH                 live session state, TTL 24h
  bot:retry:{meeting_id}   STRING               retry attempt counter, TTL 24h

Hash fields stored in bot:state:
  status            BotStatus value
  meeting_id        UUID string
  user_id           UUID string
  platform          google_meet | zoom
  meet_url          the URL the bot is joining
  attempt           current retry number (1-based)
  max_attempts      maximum allowed retries
  next_retry_at     ISO datetime for next retry (when waiting_for_host)
  rejection_reason  why bot was rejected (if bot_rejected)
  last_error        last exception message
  session_elapsed_s how long the last session ran
  created_at        ISO datetime of first attempt
  updated_at        ISO datetime of last state change

All functions are no-ops when Redis is unavailable (Redis is optional).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LOCK_TTL_S  = 7200    # 2h  — upper bound for a single bot session
_STATE_TTL_S = 86400   # 24h — keep state visible after completion


class BotStatus:
    PENDING           = "pending"            # in DB, not yet triggered
    LAUNCHING         = "launching"          # Chromium starting
    NAVIGATING        = "navigating"         # loading meet URL
    PRE_JOIN          = "pre_join"           # on pre-join screen
    JOINING           = "joining"            # clicking join button
    WAITING_ADMISSION = "waiting_admission"  # in waiting room, host hasn't admitted
    IN_MEETING        = "in_meeting"         # confirmed inside meeting
    WAITING_FOR_HOST  = "waiting_for_host"   # joined but alone, retry scheduled
    RECORDING         = "recording"          # actively recording
    STOPPING          = "stopping"           # stopping recording
    COMPLETED         = "completed"          # recording saved, transcription queued
    FAILED            = "failed"             # all retries exhausted
    HOST_ABSENT       = "host_absent"        # max retries reached, host never showed
    BOT_REJECTED      = "bot_rejected"       # host explicitly denied admission
    CANCELLED         = "cancelled"          # manually stopped

    # Human-readable messages shown in the frontend
    MESSAGES: Dict[str, str] = {
        "pending":            "Bot scheduled — waiting for meeting time",
        "launching":          "Bot is starting up…",
        "navigating":         "Bot is loading the meeting URL…",
        "pre_join":           "Bot is on the pre-join screen…",
        "joining":            "Bot is joining the meeting…",
        "waiting_admission":  "Waiting for host to admit the bot…",
        "in_meeting":         "Bot is in the meeting",
        "waiting_for_host":   "Host hasn't started yet — will retry shortly",
        "recording":          "Bot is recording",
        "stopping":           "Bot is finishing the recording…",
        "completed":          "Meeting recorded — processing transcript",
        "failed":             "Bot failed to join — check logs",
        "host_absent":        "Host never started the meeting",
        "bot_rejected":       "Host rejected the bot from the meeting",
        "cancelled":          "Bot was manually stopped",
    }

    @classmethod
    def message(cls, status: str) -> str:
        return cls.MESSAGES.get(status, status)


async def acquire_lock(meeting_id: str) -> bool:
    """
    Try to acquire the bot dedup lock for this meeting.
    Returns True if we got it (safe to proceed), False if another bot is already running.
    """
    from app.core.redis import redis_client
    if not redis_client:
        return True  # no Redis → no dedup, allow through
    key = f"bot:lock:{meeting_id}"
    result = await redis_client.set(key, "1", nx=True, ex=_LOCK_TTL_S)
    return result is not None


async def release_lock(meeting_id: str) -> None:
    """Release the dedup lock. Call after session completes or fails permanently."""
    from app.core.redis import redis_client
    if redis_client:
        await redis_client.delete(f"bot:lock:{meeting_id}")


async def set_bot_state(meeting_id: str, status: str, **extra) -> None:
    """
    Persist bot state. Extra kwargs become additional hash fields.
    Preserves fields already in the hash (only overwrites status + updated_at + extras).
    Example: set_bot_state(mid, BotStatus.JOINING, attempt=2, user_id=uid)
    """
    from app.core.redis import redis_client
    if not redis_client:
        return
    key = f"bot:state:{meeting_id}"
    now_iso = datetime.now(timezone.utc).isoformat()
    data: Dict[str, str] = {
        "status":     status,
        "meeting_id": meeting_id,
        "updated_at": now_iso,
    }
    for k, v in extra.items():
        if v is not None:
            data[k] = str(v)
    # Set created_at only on first write
    exists = await redis_client.exists(key)
    if not exists:
        data["created_at"] = now_iso
    await redis_client.hset(key, mapping=data)
    await redis_client.expire(key, _STATE_TTL_S)
    logger.debug("[BotState] meeting=%s  status=%s  extra=%s", meeting_id, status, extra)


async def set_rich_state(
    meeting_id: str,
    status: str,
    *,
    user_id: Optional[str]       = None,
    platform: Optional[str]      = None,
    meet_url: Optional[str]      = None,
    attempt: Optional[int]       = None,
    max_attempts: Optional[int]  = None,
    next_retry_at: Optional[str] = None,
    rejection_reason: Optional[str] = None,
    last_error: Optional[str]    = None,
    session_elapsed_s: Optional[float] = None,
) -> None:
    """
    Convenience wrapper for setting the full canonical bot state object.
    Use this from auto_join.py so the frontend always gets a consistent shape.
    """
    extra: Dict[str, Any] = {}
    if user_id          is not None: extra["user_id"]           = user_id
    if platform         is not None: extra["platform"]          = platform
    if meet_url         is not None: extra["meet_url"]          = meet_url
    if attempt          is not None: extra["attempt"]           = attempt
    if max_attempts     is not None: extra["max_attempts"]      = max_attempts
    if next_retry_at    is not None: extra["next_retry_at"]     = next_retry_at
    if rejection_reason is not None: extra["rejection_reason"]  = rejection_reason
    if last_error       is not None: extra["last_error"]        = last_error
    if session_elapsed_s is not None: extra["session_elapsed_s"] = round(session_elapsed_s)
    await set_bot_state(meeting_id, status, **extra)


async def get_bot_state(meeting_id: str) -> Optional[Dict[str, Any]]:
    """Return the current bot state dict, or None if no state exists."""
    from app.core.redis import redis_client
    if not redis_client:
        return None
    key = f"bot:state:{meeting_id}"
    data = await redis_client.hgetall(key)
    return dict(data) if data else None


async def get_full_state(meeting_id: str) -> Dict[str, Any]:
    """
    Return a richly shaped state dict safe to send directly to the frontend.
    Includes human-readable message, numeric attempt/max_attempts, and
    next_retry_at for countdown display.
    Always returns a dict (never None) — falls back to {status: "unknown"}.
    """
    raw = await get_bot_state(meeting_id)
    if not raw:
        return {
            "meeting_id":  meeting_id,
            "status":      "unknown",
            "message":     "No bot state found",
            "attempt":     0,
            "max_attempts": 0,
        }

    status = raw.get("status", "unknown")
    return {
        "meeting_id":       meeting_id,
        "status":           status,
        "message":          BotStatus.message(status),
        "attempt":          _int(raw.get("attempt", 0)),
        "max_attempts":     _int(raw.get("max_attempts", 0)),
        "next_retry_at":    raw.get("next_retry_at"),
        "rejection_reason": raw.get("rejection_reason"),
        "last_error":       raw.get("last_error"),
        "user_id":          raw.get("user_id"),
        "platform":         raw.get("platform"),
        "meet_url":         raw.get("meet_url"),
        "session_elapsed_s": _int(raw.get("session_elapsed_s", 0)),
        "created_at":       raw.get("created_at"),
        "updated_at":       raw.get("updated_at"),
    }


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


async def increment_retry(meeting_id: str) -> int:
    """Increment retry counter and return the new value (1-based)."""
    from app.core.redis import redis_client
    if not redis_client:
        return 0
    key = f"bot:retry:{meeting_id}"
    count = await redis_client.incr(key)
    await redis_client.expire(key, _STATE_TTL_S)
    return int(count)


async def get_retry_count(meeting_id: str) -> int:
    from app.core.redis import redis_client
    if not redis_client:
        return 0
    key = f"bot:retry:{meeting_id}"
    val = await redis_client.get(key)
    return int(val) if val else 0


async def clear_state(meeting_id: str) -> None:
    """Remove all Redis state for a meeting. Call after permanent completion."""
    from app.core.redis import redis_client
    if redis_client:
        await redis_client.delete(
            f"bot:state:{meeting_id}",
            f"bot:retry:{meeting_id}",
        )

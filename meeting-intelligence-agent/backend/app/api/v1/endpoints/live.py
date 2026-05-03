"""
Live meeting endpoints — WebSocket and SSE.

WebSocket:  WS  /api/v1/live/ws/{meeting_id}?token=<jwt>
SSE:        GET /api/v1/live/{meeting_id}/stream   (Authorization: Bearer <jwt>)

Both subscribe to the Redis pub/sub channel "live:{meeting_id}" and forward
every JSON event to the connected client.

Event shapes (published by LivePipeline in streaming_transcription.py):
  {"type": "transcript", "text": "...", "speaker": null, "t": 12.5, "final": true}
  {"type": "mention",    "name": "...", "text": "...", "t": 15.2}
  {"type": "status",     "status": "recording", "elapsed_s": 0}
  {"type": "heartbeat",  "elapsed_s": 30}

Authentication:
  WS:  pass JWT as ?token= query param (browsers can't set Authorization on WS).
  SSE: standard Authorization: Bearer header.
  Both paths accept ?token= for convenience.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.meeting import Meeting
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

# How long SSE/WS will stay open with no Redis events before closing.
STREAM_IDLE_TIMEOUT_S = 300


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _user_from_token(token: str, db: Session) -> Optional[User]:
    try:
        payload = decode_token(token)
        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            return None
        return db.query(User).filter(User.id == str(user_id)).first()
    except Exception:
        return None


def _check_meeting_access(meeting_id: str, user: User, db: Session) -> Meeting:
    """Return meeting or raise 403/404."""
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if str(meeting.organization_id) != str(user.organization_id):
        raise HTTPException(status_code=403, detail="Access denied")
    return meeting


# ── Redis subscription helper ─────────────────────────────────────────────────

async def _redis_event_generator(
    meeting_id: str,
) -> AsyncGenerator[dict, None]:
    """
    Subscribe to Redis pub/sub channel for this meeting and yield parsed dicts.
    Yields None on heartbeat timeout so callers can send keepalive pings.
    Exits when the pipeline sends {"type": "status", "status": "stopped"}.
    """
    from app.core.redis import redis_client

    if not redis_client:
        yield {"type": "error", "message": "Redis unavailable — live feed disabled"}
        return

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"live:{meeting_id}")

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=STREAM_IDLE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                # No event for STREAM_IDLE_TIMEOUT_S — close stream
                return

            if message is None:
                # No message yet — yield a keepalive signal (None means no data)
                yield None
                continue

            if message["type"] != "message":
                continue

            try:
                event = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            yield event

            # Close when pipeline signals it stopped
            if event.get("type") == "status" and event.get("status") == "stopped":
                return

    finally:
        try:
            await pubsub.unsubscribe(f"live:{meeting_id}")
            await pubsub.aclose()
        except Exception:
            pass


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/{meeting_id}")
async def live_ws(
    websocket: WebSocket,
    meeting_id: str,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> None:
    """
    WebSocket live feed for a meeting.
    Authenticate via ?token=<jwt>.
    """
    await websocket.accept()

    if not token:
        await websocket.send_json({"type": "error", "message": "token required"})
        await websocket.close(code=4001)
        return

    user = _user_from_token(token, db)
    if not user:
        await websocket.send_json({"type": "error", "message": "invalid token"})
        await websocket.close(code=4001)
        return

    try:
        _check_meeting_access(meeting_id, user, db)
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "message": exc.detail})
        await websocket.close(code=4003)
        return

    logger.info("[LiveWS] user=%s connected  meeting=%s", user.id, meeting_id)

    try:
        async for event in _redis_event_generator(meeting_id):
            if event is None:
                # Keepalive ping
                try:
                    await websocket.send_json({"type": "ping"})
                except WebSocketDisconnect:
                    break
                continue

            try:
                await websocket.send_json(event)
            except WebSocketDisconnect:
                break

            if event.get("type") == "status" and event.get("status") == "stopped":
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[LiveWS] error  meeting=%s: %s", meeting_id, exc)
    finally:
        logger.info("[LiveWS] user=%s disconnected  meeting=%s", user.id, meeting_id)
        try:
            await websocket.close()
        except Exception:
            pass


# ── SSE endpoint ──────────────────────────────────────────────────────────────

@router.get("/{meeting_id}/stream")
async def live_sse(
    meeting_id: str,
    request: Request,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Server-Sent Events live feed for a meeting.
    Authenticate via Authorization: Bearer header or ?token= query param.
    """
    # Resolve token from header or query param
    auth_header = request.headers.get("authorization", "")
    resolved_token = token
    if not resolved_token and auth_header.lower().startswith("bearer "):
        resolved_token = auth_header[7:].strip()

    if not resolved_token:
        raise HTTPException(status_code=401, detail="token required")

    user = _user_from_token(resolved_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="invalid token")

    _check_meeting_access(meeting_id, user, db)

    logger.info("[LiveSSE] user=%s connected  meeting=%s", user.id, meeting_id)

    async def _event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in _redis_event_generator(meeting_id):
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                if event is None:
                    # SSE comment line acts as keepalive
                    yield ": keepalive\n\n"
                    continue

                yield f"data: {json.dumps(event)}\n\n"

                if event.get("type") == "status" and event.get("status") == "stopped":
                    break
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[LiveSSE] user=%s disconnected  meeting=%s", user.id, meeting_id)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


# ── Status endpoint (REST polling fallback) ───────────────────────────────────

@router.get("/{meeting_id}/status")
async def live_status(
    meeting_id: str,
    token: Optional[str] = Query(default=None),
    request: Request = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    REST fallback: returns current pipeline status from Redis.
    Returns {"active": false} when no pipeline is running.
    """
    auth_header = (request.headers.get("authorization", "") if request else "")
    resolved_token = token
    if not resolved_token and auth_header.lower().startswith("bearer "):
        resolved_token = auth_header[7:].strip()
    if not resolved_token:
        raise HTTPException(status_code=401, detail="token required")

    user = _user_from_token(resolved_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="invalid token")

    _check_meeting_access(meeting_id, user, db)

    from app.services.streaming_transcription import get_pipeline
    pipeline = get_pipeline(meeting_id)

    return {
        "meeting_id": meeting_id,
        "active": pipeline is not None and pipeline._running,
        "elapsed_s": int(pipeline._elapsed_s) if pipeline else 0,
        "segments": pipeline._total_segments if pipeline else 0,
    }

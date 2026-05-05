"""
Live transcription + user-aware intelligence pipeline.

Every CHUNK_INTERVAL_S seconds:
  1. page.evaluate(EXTRACT_CHUNK_SCRIPT) → base64 webm audio
  2. Groq Whisper API                    → transcript text
  3. _detect_mentions()                  → name mentions      (< 1 ms)
  4. RealTimeIntelligence.process()      → action items, decisions,
                                           confirmations       (< 1 ms regex,
                                                                async LLM every 60 s)
  5. redis.publish("live:{mid}")         → all events to WS endpoint

On stop():
  6. Persist detected action items + decisions to DB so the existing
     follow-up / reminder system picks them up automatically.

Redis channel events:
  transcript   — {type, text, speaker, t, final}
  mention      — {type, name, text, t}
  action_item  — {type, event_id, text, assignee, target_user_id,
                  priority, confirmed, confidence, source, t}
  decision     — {type, event_id, text, priority, confidence, source, t}
  confirmation — {type, event_id, action_event_id, action_text, t}
  event_enrichment — {type, event_id, related_to_previous}
  status       — {type, status, elapsed_s}
  heartbeat    — {type, elapsed_s}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

CHUNK_INTERVAL_S  = 5
WHISPER_TIMEOUT_S = 10
HEARTBEAT_EVERY_S = 30
MIN_CHUNK_BYTES   = 2048


class LivePipeline:
    """
    Per-meeting live transcription + intelligence pipeline.

    Constructor params:
        meeting_id       — meeting UUID string
        page             — Playwright page object
        attendee_names   — display names for mention + assignee detection
        attendee_map     — AttendeeMap (name→user_id) for user context
        current_user_id  — session owner's user_id (for priority=critical)
        current_user_name— session owner's full_name
        organization_id  — for cross-meeting memory queries + DB persistence
    """

    def __init__(
        self,
        meeting_id: str,
        page: Any,
        attendee_names: Optional[List[str]] = None,
        attendee_map=None,
        current_user_id: Optional[str] = None,
        current_user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> None:
        self.meeting_id        = meeting_id
        self.page              = page
        self.attendee_names    = attendee_names or []
        self._attendee_map     = attendee_map
        self.current_user_id   = current_user_id
        self.current_user_name = current_user_name
        self.organization_id   = organization_id

        self._task: Optional[asyncio.Task] = None
        self._running        = False
        self._elapsed_s      = 0.0
        self._total_segments = 0
        self._intel: Optional[Any]  = None
        self._rec_engine: Optional[Any] = None

        # Accumulate detected events for DB persistence on stop()
        self._detected_action_items: List[dict] = []
        self._detected_decisions:    List[dict] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(
            self._loop(),
            name=f"live_pipeline:{self.meeting_id}",
        )
        logger.info("[LivePipeline] started  meeting=%s  user=%s",
                    self.meeting_id, self.current_user_id)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        await self._publish({
            "type": "status", "status": "stopped",
            "elapsed_s": int(self._elapsed_s),
        })
        logger.info("[LivePipeline] stopped  meeting=%s  segments=%d  actions=%d  decisions=%d",
                    self.meeting_id, self._total_segments,
                    len(self._detected_action_items), len(self._detected_decisions))

        # Persist real-time detected items to DB (background, non-blocking)
        if self._detected_action_items or self._detected_decisions:
            asyncio.create_task(self._persist_to_db())

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            from app.services.intelligence import RealTimeIntelligence
            from app.services.recommendations import RecommendationEngine
            self._intel = RealTimeIntelligence(
                meeting_id=self.meeting_id,
                attendee_names=self.attendee_names,
                attendee_map=self._attendee_map,
                current_user_id=self.current_user_id,
                current_user_name=self.current_user_name,
                organization_id=self.organization_id,
            )
            self._rec_engine = RecommendationEngine(
                meeting_id=self.meeting_id,
                current_user_id=self.current_user_id,
                current_user_name=self.current_user_name,
            )
            logger.info("[LivePipeline] intelligence + recommendation engine enabled  meeting=%s", self.meeting_id)
        except Exception as exc:
            logger.warning("[LivePipeline] intelligence unavailable: %s", exc)
            self._intel = None
            self._rec_engine = None

        start_wall = time.monotonic()
        last_heartbeat = 0.0

        await self._publish({"type": "status", "status": "recording", "elapsed_s": 0})

        while self._running:
            await asyncio.sleep(CHUNK_INTERVAL_S)
            self._elapsed_s = time.monotonic() - start_wall

            if self._elapsed_s - last_heartbeat >= HEARTBEAT_EVERY_S:
                await self._publish({"type": "heartbeat", "elapsed_s": int(self._elapsed_s)})
                last_heartbeat = self._elapsed_s

            try:
                chunk_bytes = await self._extract_chunk()
            except Exception as exc:
                logger.debug("[LivePipeline] chunk extract error: %s", exc)
                continue

            if not chunk_bytes or len(chunk_bytes) < MIN_CHUNK_BYTES:
                continue

            try:
                text = await asyncio.wait_for(
                    self._transcribe(chunk_bytes),
                    timeout=WHISPER_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.debug("[LivePipeline] transcription timed out")
                continue
            except Exception as exc:
                logger.debug("[LivePipeline] transcription error: %s", exc)
                continue

            if not text or not text.strip():
                continue

            self._total_segments += 1
            clean = text.strip()
            t = round(self._elapsed_s, 1)

            await self._publish({
                "type": "transcript", "text": clean,
                "speaker": None, "t": t, "final": True,
            })

            # Mention detection
            for mention in _detect_mentions(clean, self.attendee_names):
                await self._publish({
                    "type": "mention", "name": mention["name"],
                    "text": clean, "t": t,
                })

            # Intelligence layer — runs first so its events are available
            # to record_action_item() inside the recommendation engine
            intel_events: List[dict] = []
            if self._intel is not None:
                try:
                    intel_events = await self._intel.process(clean, self._elapsed_s)
                    for ev in intel_events:
                        await self._publish(ev)
                        # Accumulate for DB persistence
                        if ev["type"] == "action_item":
                            self._detected_action_items.append(ev)
                        elif ev["type"] == "decision":
                            self._detected_decisions.append(ev)
                except Exception as exc:
                    logger.debug("[LivePipeline] intelligence error: %s", exc)

            # Recommendation engine — evaluates after intelligence so it can
            # record action items assigned to the current user and suppress
            # redundant join_now signals
            if self._rec_engine is not None:
                try:
                    rec = self._rec_engine.process_segment(
                        clean, self._elapsed_s, self._publish_to_redis,
                    )
                    # Record action items assigned to current user so the
                    # engine suppresses redundant join_now signals
                    if rec and rec.action == "join_now":
                        for ai_ev in intel_events:
                            if ai_ev.get("type") == "action_item":
                                target = ai_ev.get("target_user_id")
                                if target and target == self.current_user_id:
                                    self._rec_engine.record_action_item(ai_ev)
                except Exception as exc:
                    logger.debug("[LivePipeline] recommendation error: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _extract_chunk(self) -> Optional[bytes]:
        from app.services.recording_service import EXTRACT_CHUNK_SCRIPT
        b64 = await self.page.evaluate(EXTRACT_CHUNK_SCRIPT)
        return base64.b64decode(b64) if b64 else None

    async def _transcribe(self, audio_bytes: bytes) -> str:
        from app.core.config import settings
        from groq import Groq as GroqClient  # type: ignore

        api_key = settings.GROQ_API_KEY or getattr(settings, "GROK_API_KEY", "")
        if not api_key:
            return ""

        loop = asyncio.get_event_loop()

        def _call():
            import tempfile
            client = GroqClient(api_key=api_key)
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                with open(tmp_path, "rb") as f:
                    result = client.audio.transcriptions.create(
                        file=("chunk.webm", f),
                        model="whisper-large-v3-turbo",
                        response_format="text",
                    )
                return result if isinstance(result, str) else (result.text if hasattr(result, "text") else "")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        return await loop.run_in_executor(None, _call)

    async def _publish(self, event: dict) -> None:
        await self._publish_to_redis(event)

    async def _publish_to_redis(self, event: dict) -> None:
        from app.core.redis import redis_client
        if not redis_client:
            return
        try:
            event.setdefault("meeting_id", self.meeting_id)
            event.setdefault("ts", datetime.now(timezone.utc).isoformat())
            await redis_client.publish(
                f"live:{self.meeting_id}",
                json.dumps(event),
            )
        except Exception as exc:
            logger.debug("[LivePipeline] redis publish error: %s", exc)

    # ── DB persistence ────────────────────────────────────────────────────────

    async def _persist_to_db(self) -> None:
        """
        Persist real-time detected items to the existing ActionItem + Decision tables.
        Runs in a thread so it doesn't block the event loop after stop().
        Items are tagged extraction_method='realtime_ai' so the batch processor
        can merge/confirm them later rather than duplicating.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._persist_sync,
            list(self._detected_action_items),
            list(self._detected_decisions),
        )

    def _persist_sync(
        self,
        action_items: List[dict],
        decisions: List[dict],
    ) -> None:
        try:
            from app.core.database import SessionLocal
            from app.models.action_item import ActionItem
            from app.models.mention import Decision
            import uuid as _uuid

            conf_score = {"high": 0.85, "medium": 0.60, "low": 0.35}
            db = SessionLocal()
            try:
                for ev in action_items:
                    ai = ActionItem(
                        id=_uuid.uuid4(),
                        organization_id=self.organization_id,
                        meeting_id=self.meeting_id,
                        title=ev["text"][:500],
                        extracted_from_text=ev.get("raw", ev["text"])[:2000],
                        confidence_score=conf_score.get(ev.get("confidence", "medium"), 0.6),
                        extraction_method="realtime_ai",
                        priority=ev.get("priority", "medium"),
                        assigned_to_user_id=ev.get("target_user_id"),
                        status="open",
                    )
                    db.add(ai)

                for ev in decisions:
                    dec = Decision(
                        id=_uuid.uuid4(),
                        meeting_id=self.meeting_id,
                        decision_text=ev["text"][:2000],
                        confidence_score=conf_score.get(ev.get("confidence", "medium"), 0.6),
                        status="decided",
                    )
                    db.add(dec)

                db.commit()
                logger.info(
                    "[LivePipeline] persisted  meeting=%s  actions=%d  decisions=%d",
                    self.meeting_id, len(action_items), len(decisions),
                )
            except Exception as exc:
                db.rollback()
                logger.warning("[LivePipeline] DB persist failed: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[LivePipeline] DB persist outer error: %s", exc)


# ── Mention detection ─────────────────────────────────────────────────────────

def _detect_mentions(text: str, attendee_names: List[str]) -> List[dict]:
    found = []
    seen: set = set()
    text_lower = text.lower()
    for full_name in attendee_names:
        if not full_name or full_name in seen:
            continue
        for part in full_name.strip().split():
            if len(part) >= 3 and part.lower() in text_lower:
                seen.add(full_name)
                found.append({"name": full_name})
                break
    return found


# ── Registry ──────────────────────────────────────────────────────────────────

_pipelines: dict[str, LivePipeline] = {}


def get_pipeline(meeting_id: str) -> Optional[LivePipeline]:
    return _pipelines.get(meeting_id)


def register_pipeline(pipeline: LivePipeline) -> None:
    _pipelines[pipeline.meeting_id] = pipeline


def unregister_pipeline(meeting_id: str) -> None:
    _pipelines.pop(meeting_id, None)

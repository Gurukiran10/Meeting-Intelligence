"""
Real-time meeting intelligence — user-aware, priority-ranked, memory-linked.

Detection pipeline per segment
────────────────────────────────
Tier 1 — Regex (< 1 ms)
  Runs on the current segment + sliding 60-second context window.
  Detects action items, decisions, and confirmations.

Tier 2 — LLM scan (async, every 60 s)
  Groq LLaMA-3 on the accumulated transcript buffer.
  Higher recall; overlapping dedup with Tier 1.

User-awareness layer (after detection)
  • Resolves assignee string → user_id via AttendeeMap
  • Computes priority: critical / important / info
    - critical  = assigned to or mentioning the session owner
    - important = high-confidence event for anyone
    - info      = low/medium confidence background item
  • target_user_id added to every event (null if unresolvable)

Confirmation detection
  • Phrases like "will do", "got it", "yes I'll handle it"
  • Looks back at recent unconfirmed action items (< 90 s)
  • Emits a separate "confirmation" event that the frontend merges

Cross-meeting memory (async, fire-and-forget)
  • After a high-confidence event, queries the DB for past items with
    matching keywords (same org, different meeting, last 90 days)
  • If found, emits an "event_enrichment" that the frontend merges
    into the original event by event_id

Event shapes added by this module:

  Action item:
  { "type": "action_item", "event_id": "abc:5",
    "text": "...", "assignee": "Ajay", "target_user_id": "uuid",
    "priority": "critical", "confirmed": false,
    "confidence": "high", "source": "regex", "t": 45.0 }

  Decision:
  { "type": "decision", "event_id": "abc:6",
    "text": "...", "target_user_id": null,
    "priority": "important", "confidence": "high", "source": "regex", "t": 60.0 }

  Confirmation:
  { "type": "confirmation", "event_id": "abc:9",
    "action_event_id": "abc:5",   # links to the confirmed action item
    "action_text": "...", "t": 90.0 }

  Event enrichment (memory link):
  { "type": "event_enrichment", "event_id": "abc:5",
    "related_to_previous": {
      "meeting_id": "...", "meeting_title": "...",
      "meeting_date": "2025-04-10", "text": "...", "event_type": "action_item" } }
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Pattern tables ────────────────────────────────────────────────────────────

_ACTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\baction item\b", re.I), "high"),
    (re.compile(
        r"\b(?:i['']ll|i will|we['']ll|we will|going to|gonna)\s+"
        r"(?:\w+\s+){0,4}"
        r"(?:do|fix|add|create|check|review|update|send|write|build|"
        r"implement|test|deploy|schedule|look into|investigate|reach out|"
        r"follow up|handle|set up|prepare|draft|confirm)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:can you|could you|would you|please)\s+"
        r"(?:\w+\s+){0,4}"
        r"(?:do|fix|add|create|check|review|update|send|write|handle|"
        r"look into|reach out|take care of|prepare|confirm)\b", re.I),
     "high"),
    (re.compile(
        r"\bneed(?:s)? to\s+(?:\w+\s+){0,3}"
        r"(?:do|fix|add|create|check|review|update|send|write|build|"
        r"implement|test|prepare|confirm)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:responsible for|will own|will take|will handle|"
        r"assign(?:ed)? to|taking on)\b", re.I),
     "high"),
    (re.compile(
        r"\bby\s+(?:tomorrow|end of day|eod|eow|end of week|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"next week|next month)\b", re.I),
     "medium"),
    (re.compile(r"\bfollow.?up\b", re.I), "medium"),
    (re.compile(r"\btodo\b", re.I), "medium"),
    (re.compile(r"\bdeadline\b", re.I), "low"),
    (re.compile(r"\bremind\s+(?:me|us|him|her|them)\b", re.I), "low"),
]

_DECISION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(?:we['']ve decided|we decided|it['']s decided|"
        r"the decision is|decision[:\s])\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:we['']re going with|going with|we['']ll go with|"
        r"let['']s go with|we['']re choosing)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:final decision|final call|final answer|final choice|"
        r"that['']s final|that['']s settled)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:we['']ve agreed|all agreed|everyone agrees|"
        r"we agree|agreed[.,!]?)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:approved|confirmed|locked in|finalized|"
        r"settled on|signed off)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:moving forward with|going ahead with|"
        r"we['']ll proceed with|proceed with)\b", re.I),
     "high"),
    (re.compile(
        r"\b(?:let['']s use|we['']ll use|we are using|"
        r"we will use|we should use|we['']re using)\b", re.I),
     "medium"),
    (re.compile(
        r"\b(?:we won['']t|we will not|not going to|"
        r"we['']re not doing|scrapping|dropping)\b", re.I),
     "medium"),
]

# Confirmation phrases — "yes I'll do it", "will do", "got it", …
_CONFIRMATION_PATTERNS: List[re.Pattern] = [
    re.compile(
        r"\b(?:yes[,!]?\s+)?i['']ll\s+(?:do it|handle it|take care of it|"
        r"fix it|update it|get it done|get on that)\b", re.I),
    re.compile(r"\b(?:will do|on it|got it|noted[!.]?|understood[!.]?|copy that)\b", re.I),
    re.compile(r"\bokay[,!]?\s+(?:i['']ll|sure|will|got it)\b", re.I),
    re.compile(r"\bsounds good[,!]?\s+i['']ll\b", re.I),
    re.compile(r"\bsure[,!]?\s+(?:thing|i['']ll|will)\b", re.I),
    re.compile(r"\byep[,!]?\s+(?:i['']ll|will|sure)\b", re.I),
    re.compile(r"\babsolutely[,!]?\s+(?:i['']ll|will)\b", re.I),
    re.compile(r"\bconsider(?:ed)? it done\b", re.I),
    re.compile(r"\bi['']m on it\b", re.I),
    re.compile(r"\bleave it to me\b", re.I),
]

_CONF_ORDER = {"high": 3, "medium": 2, "low": 1}


# ── Deduplicator ──────────────────────────────────────────────────────────────

class _Deduplicator:
    def __init__(self, ttl_s: int = 180) -> None:
        self._seen: Dict[str, float] = {}
        self._ttl = ttl_s

    def _key(self, text: str) -> str:
        normalized = re.sub(r"[^\w\s]", "", text.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()[:80]
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def is_duplicate(self, text: str) -> bool:
        now = time.monotonic()
        self._seen = {k: t for k, t in self._seen.items() if now - t <= self._ttl}
        key = self._key(text)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


# ── Context window ────────────────────────────────────────────────────────────

class ContextWindow:
    def __init__(self, maxlen: int = 12) -> None:
        self._buf: Deque[str] = deque(maxlen=maxlen)

    def push(self, text: str) -> None:
        self._buf.append(text.strip())

    @property
    def full(self) -> str:
        return " ".join(self._buf)


# ── Priority computation ──────────────────────────────────────────────────────

def _names_match(name_a: str, name_b: str) -> bool:
    """True if any word from name_a appears in name_b (case-insensitive)."""
    parts_a = name_a.lower().split()
    name_b_l = name_b.lower()
    return any(p in name_b_l for p in parts_a if len(p) >= 3)


def compute_priority(
    event_type: str,
    confidence: str,
    assignee: Optional[str],
    current_user_name: Optional[str],
    target_user_id: Optional[str],
    current_user_id: Optional[str],
) -> str:
    """
    critical  — assigned to / directed at the session owner
    important — high-confidence event involving anyone
    info      — background / low-confidence event
    """
    if event_type == "action_item":
        # Direct assignment to session owner → critical
        if target_user_id and current_user_id and target_user_id == current_user_id:
            return "critical"
        if assignee and current_user_name and _names_match(assignee, current_user_name):
            return "critical"
        return "important" if confidence == "high" else "info"

    if event_type == "decision":
        return "important" if confidence == "high" else "info"

    if event_type == "mention":
        return "important"

    return "info"


# ── Low-level detection ───────────────────────────────────────────────────────

@dataclass
class DetectedItem:
    item_type: str
    text: str
    raw: str
    confidence: str
    assignee: Optional[str] = None
    source: str = "regex"


def _phrase_around(match: re.Match, full_text: str, pre: int = 40, post: int = 100) -> str:
    start = max(0, match.start() - pre)
    end   = min(len(full_text), match.end() + post)
    return re.sub(r"\s+", " ", full_text[start:end]).strip()


def _extract_assignee_from_text(text: str, attendee_names: List[str]) -> Optional[str]:
    text_lower = text.lower()
    for full_name in attendee_names:
        if not full_name:
            continue
        for part in full_name.lower().split():
            if len(part) < 3:
                continue
            if re.search(
                rf"\b{re.escape(part)}\s+(?:will|should|needs? to|can you|could you)\b",
                text_lower,
            ):
                return full_name
            if re.search(
                rf"\bassign(?:ed)?\s+to\s+{re.escape(part)}\b",
                text_lower,
            ):
                return full_name
    # Fallback: any name anywhere in text
    for full_name in attendee_names:
        for part in (full_name or "").lower().split():
            if len(part) >= 3 and part in text_lower:
                return full_name
    return None


def detect_action_items(
    text: str,
    attendee_names: Optional[List[str]] = None,
    context: Optional[str] = None,
) -> List[DetectedItem]:
    search = context or text
    results: List[DetectedItem] = []
    seen: set = set()
    for pattern, confidence in _ACTION_PATTERNS:
        for m in pattern.finditer(search):
            phrase = _phrase_around(m, search)
            key = phrase[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            assignee = _extract_assignee_from_text(phrase, attendee_names or [])
            results.append(DetectedItem("action_item", phrase, text, confidence, assignee))
    return results


def detect_decisions(
    text: str,
    context: Optional[str] = None,
) -> List[DetectedItem]:
    search = context or text
    results: List[DetectedItem] = []
    seen: set = set()
    for pattern, confidence in _DECISION_PATTERNS:
        for m in pattern.finditer(search):
            phrase = _phrase_around(m, search, pre=20, post=120)
            key = phrase[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(DetectedItem("decision", phrase, text, confidence))
    return results


def detect_confirmation(text: str) -> bool:
    """Return True if text contains a confirmation phrase."""
    return any(p.search(text) for p in _CONFIRMATION_PATTERNS)


# ── Periodic LLM scan ─────────────────────────────────────────────────────────

LLM_SCAN_INTERVAL_S  = 60
LLM_MIN_NEW_SEGMENTS = 5

_LLM_PROMPT = """\
You are a meeting assistant. Extract action items and decisions from the transcript below.
Return ONLY a JSON array. Each element must have:
  "type": "action_item" or "decision"
  "text": the exact phrase (under 120 chars)
  "assignee": person name if mentioned, else null  (action_item only)
  "confidence": "high" or "medium"

Return [] if nothing found.

Transcript:
{transcript}
"""


async def llm_scan(
    transcript: str,
    attendee_names: Optional[List[str]] = None,
) -> List[DetectedItem]:
    try:
        from app.core.config import settings
        from groq import Groq as GroqClient  # type: ignore

        api_key = settings.GROQ_API_KEY or getattr(settings, "GROK_API_KEY", "")
        if not api_key:
            return []

        model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
        prompt = _LLM_PROMPT.format(transcript=transcript[:3000])

        loop = asyncio.get_event_loop()

        def _call() -> str:
            client = GroqClient(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.1,
            )
            return resp.choices[0].message.content or "[]"

        raw = await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=8)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)

        items = json.loads(raw)
        results: List[DetectedItem] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in ("action_item", "decision"):
                continue
            results.append(DetectedItem(
                item_type=item["type"],
                text=str(item.get("text", ""))[:200],
                raw="[llm scan]",
                confidence=item.get("confidence", "medium"),
                assignee=item.get("assignee"),
                source="llm",
            ))
        return results
    except Exception as exc:
        logger.debug("[intelligence] llm_scan error: %s", exc)
        return []


# ── RealTimeIntelligence — stateful per-meeting processor ────────────────────

@dataclass
class _PendingActionItem:
    """Tracks a recently detected, unconfirmed action item for confirmation matching."""
    event_id: str
    text: str
    detected_at: float   # monotonic


class RealTimeIntelligence:
    """
    Owns all per-meeting state.  One instance per LivePipeline.

    Constructor params:
        meeting_id        — meeting UUID string
        attendee_names    — list of display names (for assignee extraction)
        attendee_map      — AttendeeMap for name→user_id resolution
        current_user_id   — the session owner's user_id
        current_user_name — the session owner's full_name
        organization_id   — for cross-meeting memory queries
    """

    def __init__(
        self,
        meeting_id: str,
        attendee_names: Optional[List[str]] = None,
        attendee_map=None,           # AttendeeMap | None
        current_user_id: Optional[str] = None,
        current_user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> None:
        self.meeting_id        = meeting_id
        self.attendee_names    = attendee_names or []
        self._attendee_map     = attendee_map       # may be None
        self.current_user_id   = current_user_id
        self.current_user_name = current_user_name
        self.organization_id   = organization_id

        self._context         = ContextWindow(maxlen=12)
        self._dedup_ai        = _Deduplicator(ttl_s=180)
        self._dedup_dec       = _Deduplicator(ttl_s=180)
        self._llm_buffer: List[str] = []
        self._last_llm_scan   = 0.0
        self._segments_since  = 0
        self._event_counter   = 0
        self._recent_pending: Deque[_PendingActionItem] = deque(maxlen=5)

    # ── Public ────────────────────────────────────────────────────────────────

    async def process(self, text: str, elapsed_s: float) -> List[dict]:
        """Process one transcript segment. Returns event dicts ready to publish."""
        self._context.push(text)
        self._llm_buffer.append(text)
        self._segments_since += 1
        context = self._context.full
        events: List[dict] = []

        # Tier 1 — action items
        for item in detect_action_items(text, self.attendee_names, context):
            if self._dedup_ai.is_duplicate(item.text):
                continue
            ev = self._build_event(item, elapsed_s)
            events.append(ev)
            self._recent_pending.append(
                _PendingActionItem(ev["event_id"], ev["text"], time.monotonic())
            )
            logger.info(
                "[intelligence] action_item  meeting=%s  priority=%s  assignee=%s",
                self.meeting_id, ev["priority"], ev.get("assignee"),
            )
            # Fire memory enrichment in background — doesn't block publish
            asyncio.create_task(self._enrich_with_memory(ev["event_id"], item.text, "action_item"))

        # Tier 1 — decisions
        for item in detect_decisions(text, context):
            if self._dedup_dec.is_duplicate(item.text):
                continue
            ev = self._build_event(item, elapsed_s)
            events.append(ev)
            logger.info(
                "[intelligence] decision  meeting=%s  priority=%s",
                self.meeting_id, ev["priority"],
            )
            asyncio.create_task(self._enrich_with_memory(ev["event_id"], item.text, "decision"))

        # Confirmation check — did this segment confirm a recent action item?
        if detect_confirmation(text):
            conf_ev = self._build_confirmation(text, elapsed_s)
            if conf_ev:
                events.append(conf_ev)

        # Tier 2 — periodic LLM scan
        now = time.monotonic()
        if (
            self._segments_since >= LLM_MIN_NEW_SEGMENTS
            and now - self._last_llm_scan >= LLM_SCAN_INTERVAL_S
        ):
            self._last_llm_scan = now
            self._segments_since = 0
            asyncio.create_task(self._run_llm_scan(elapsed_s))

        return events

    # ── Internal ──────────────────────────────────────────────────────────────

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"{self.meeting_id[:8]}:{self._event_counter}"

    def _resolve_target(self, assignee: Optional[str]) -> Optional[str]:
        """Resolve assignee name → user_id via AttendeeMap."""
        if not assignee or not self._attendee_map:
            return None
        hit = self._attendee_map.resolve(assignee)
        return hit.user_id if hit else None

    def _build_event(self, item: DetectedItem, elapsed_s: float) -> dict:
        event_id      = self._next_event_id()
        target_uid    = self._resolve_target(item.assignee)
        priority      = compute_priority(
            item.item_type, item.confidence,
            item.assignee, self.current_user_name,
            target_uid, self.current_user_id,
        )
        ev: dict = {
            "type":             item.item_type,
            "event_id":         event_id,
            "text":             item.text,
            "raw":              item.raw[:200],
            "t":                round(elapsed_s, 1),
            "confidence":       item.confidence,
            "source":           item.source,
            "priority":         priority,
            "confirmed":        False,
            "related_to_previous": None,
            "meeting_id":       self.meeting_id,
        }
        if item.item_type == "action_item":
            ev["assignee"]       = item.assignee
            ev["target_user_id"] = target_uid
        return ev

    def _build_confirmation(self, text: str, elapsed_s: float) -> Optional[dict]:
        """Link this confirmation phrase to the most recent pending action item."""
        now = time.monotonic()
        # Find most recent unconfirmed item within 90 s
        for pending in reversed(self._recent_pending):
            if now - pending.detected_at <= 90:
                return {
                    "type":            "confirmation",
                    "event_id":        self._next_event_id(),
                    "action_event_id": pending.event_id,
                    "action_text":     pending.text,
                    "raw":             text.strip(),
                    "t":               round(elapsed_s, 1),
                    "meeting_id":      self.meeting_id,
                }
        return None

    async def _enrich_with_memory(
        self, event_id: str, text: str, event_type: str
    ) -> None:
        """Background task: look up past related events and publish enrichment."""
        if not self.organization_id:
            return
        try:
            from app.services.memory import find_related_async
            from app.core.redis import redis_client
            from datetime import datetime, timezone
            import json as _json

            related = await find_related_async(
                text, event_type, self.meeting_id, self.organization_id
            )
            if not related or not redis_client:
                return

            enrichment = {
                "type":               "event_enrichment",
                "event_id":           event_id,
                "original_type":      event_type,
                "related_to_previous": related,
                "meeting_id":         self.meeting_id,
                "ts":                 datetime.now(timezone.utc).isoformat(),
            }
            await redis_client.publish(
                f"live:{self.meeting_id}",
                _json.dumps(enrichment),
            )
        except Exception as exc:
            logger.debug("[intelligence] memory enrichment error: %s", exc)

    async def _run_llm_scan(self, elapsed_s: float) -> None:
        """Background LLM scan; publishes any new events found."""
        try:
            transcript = " ".join(self._llm_buffer[-30:])
            self._llm_buffer = self._llm_buffer[-10:]
            items = await llm_scan(transcript, self.attendee_names)

            from app.core.redis import redis_client
            from datetime import datetime, timezone
            import json as _json

            for item in items:
                dedup = self._dedup_ai if item.item_type == "action_item" else self._dedup_dec
                if dedup.is_duplicate(item.text):
                    continue
                ev = self._build_event(item, elapsed_s)
                if redis_client:
                    ev.setdefault("ts", datetime.now(timezone.utc).isoformat())
                    await redis_client.publish(
                        f"live:{self.meeting_id}", _json.dumps(ev)
                    )
        except Exception as exc:
            logger.debug("[intelligence] llm scan task error: %s", exc)

    # ── Persistence helpers (called from LivePipeline.stop) ──────────────────

    def get_detected_action_items(self) -> List[dict]:
        """Return all unique action item events detected so far."""
        return [ev for ev in self._get_all_events() if ev["type"] == "action_item"]

    def get_detected_decisions(self) -> List[dict]:
        return [ev for ev in self._get_all_events() if ev["type"] == "decision"]

    def _get_all_events(self) -> List[dict]:
        # Events are not stored here — we store them in streaming_transcription
        return []

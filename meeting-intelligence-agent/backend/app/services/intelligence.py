"""
Real-time meeting intelligence — explainable, user-aware, proactive.

Pipeline per segment
────────────────────
Tier 1 — Regex
  Runs on sliding context window. Produces DetectedItem with:
    - detection_reason   — which pattern matched and why
    - detection_method   — "regex"

Tier 2 — LLM scan (async, every 60 s)
  Produces DetectedItem with detection_method = "llm"
  and a longer reasoning string from the model.

Post-detection (every item):
  • resolve assignee → target_user_id via AttendeeMap
  • compute priority + reason + explanation
  • compute confidence_score (0–1 float)
  • set urgency_flag if target_user is absent

Event schema
────────────
Every event now carries:
  priority, reason, explanation          ← why this matters
  confidence_score (0–1)                 ← how sure we are
  detection_method ("regex"|"llm")       ← how it was found
  urgency_flag                           ← needs immediate attention
  related_context                        ← sentence that triggered it

Action item example:
{
  "type": "action_item", "event_id": "abc:5",
  "text": "Ajay will update the dashboard by Friday",
  "assignee": "Ajay", "target_user_id": "uuid",
  "priority": "critical",
  "reason": "Assigned directly to you",
  "explanation": "Sanjay explicitly assigned this task to Ajay during the meeting",
  "confidence_score": 0.92,
  "detection_method": "regex",
  "urgency_flag": true,
  "related_context": "...Sanjay: Ajay will update the dashboard by Friday...",
  "t": 45.0
}

Decision example:
{
  "type": "decision", "event_id": "abc:6",
  "text": "We're going with the monorepo approach",
  "priority": "important",
  "reason": "High-confidence strategic decision",
  "explanation": "Team agreed to move forward with monorepo; confirmed by multiple voices",
  "confidence_score": 0.88,
  "detection_method": "llm",
  "urgency_flag": false,
  "related_context": "...we're going with the monorepo...",
  "t": 60.0
}

Interrupt event (absent target user):
{
  "type": "interrupt",
  "event_id": "abc:7",
  "action_event_id": "abc:5",
  "action_text": "Ajay will update the dashboard by Friday",
  "assignee": "Ajay",
  "reason": "Assigned to you but you are not in the meeting",
  "explanation": "Sanjay assigned this task to Ajay 3 minutes ago. Ajay has not joined yet.",
  "t": 45.0,
  "urgency_flag": true
}
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

# ── Confidence scores per pattern ────────────────────────────────────────────

# regex patterns → (confidence_float, explanation_template)
_ACTION_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r"\baction item\b", re.I), 0.95,
     "Explicitly labelled 'action item' in the meeting"),
    (re.compile(
        r"\b(?:i['']ll|i will|we['']ll|we will|going to|gonna)\s+"
        r"(?:\w+\s+){0,4}"
        r"(?:do|fix|add|create|check|review|update|send|write|build|"
        r"implement|test|deploy|schedule|look into|investigate|reach out|"
        r"follow up|handle|set up|prepare|draft|confirm)\b", re.I),
     0.92,
     "Future commitment phrase detected — 'will' or 'going to' + action verb"),
    (re.compile(
        r"\b(?:can you|could you|would you|please)\s+"
        r"(?:\w+\s+){0,4}"
        r"(?:do|fix|add|create|check|review|update|send|write|handle|"
        r"look into|reach out|take care of|prepare|confirm)\b", re.I),
     0.90,
     "Direct request to a named person with an action verb"),
    (re.compile(
        r"\bneed(?:s)? to\s+(?:\w+\s+){0,3}"
        r"(?:do|fix|add|create|check|review|update|send|write|build|"
        r"implement|test|prepare|confirm)\b", re.I),
     0.88,
     "'Need to' obligation pattern — strong commitment signal"),
    (re.compile(
        r"\b(?:responsible for|will own|will take|will handle|"
        r"assign(?:ed)? to|taking on)\b", re.I),
     0.93,
     "Explicit ownership assignment phrase detected"),
    (re.compile(
        r"\bby\s+(?:tomorrow|end of day|eod|eow|end of week|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"next week|next month)\b", re.I),
     0.78,
     "Deadline phrase attached — time-bound commitment makes this more important"),
    (re.compile(r"\bfollow.?up\b", re.I), 0.72,
     "'Follow up' phrase — indicates a tracked action"),
    (re.compile(r"\btodo\b", re.I), 0.65,
     "Generic 'todo' keyword — lower confidence without more context"),
    (re.compile(r"\bdeadline\b", re.I), 0.55,
     "'Deadline' mentioned — may be a task but confidence is low"),
    (re.compile(r"\bremind\s+(?:me|us|him|her|them)\b", re.I), 0.60,
     "'Remind' request — future task commitment"),
]

_DECISION_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(
        r"\b(?:we['']ve decided|we decided|it['']s decided|"
        r"the decision is|decision[:\s])\b", re.I),
     0.94,
     "Explicit 'decided' statement — direct decision confirmation"),
    (re.compile(
        r"\b(?:we['']re going with|going with|we['']ll go with|"
        r"let['']s go with|we['']re choosing)\b", re.I),
     0.91,
     "Commitment to a specific approach — 'going with' signals a decision"),
    (re.compile(
        r"\b(?:final decision|final call|final answer|final choice|"
        r"that['']s final|that['']s settled)\b", re.I),
     0.96,
     "Finality language — 'final' or 'settled' means no more debate"),
    (re.compile(
        r"\b(?:we['']ve agreed|all agreed|everyone agrees|"
        r"we agree|agreed[.,!]?)\b", re.I),
     0.89,
     "Group agreement phrase — broad consensus on a decision"),
    (re.compile(
        r"\b(?:approved|confirmed|locked in|finalized|"
        r"settled on|signed off)\b", re.I),
     0.90,
     "Formal approval word — 'approved', 'locked in', 'signed off'"),
    (re.compile(
        r"\b(?:moving forward with|going ahead with|"
        r"we['']ll proceed with|proceed with)\b", re.I),
     0.87,
     "'Proceed with' — signals a decision is being acted on"),
    (re.compile(
        r"\b(?:let['']s use|we['']ll use|we are using|"
        r"we will use|we should use|we['']re using)\b", re.I),
     0.82,
     "Technology or approach selection — 'using' or 'use' signals a decision"),
    (re.compile(
        r"\b(?:we won['']t|we will not|not going to|"
        r"we['']re not doing|scrapping|dropping)\b", re.I),
     0.85,
     "Explicit rejection or removal decision — 'won't' or 'dropping'"),
]

# Confirmation phrases
_CONFIRMATION_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(?:yes[,!]?\s+)?i['']ll\s+(?:do it|handle it|take care of it|"
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


# ── Priority computation ───────────────────────────────────────────────────────

def _names_match(name_a: str, name_b: str) -> bool:
    parts_a = name_a.lower().split()
    name_b_l = name_b.lower()
    return any(p in name_b_l for p in parts_a if len(p) >= 3)


def compute_priority_and_reason(
    item_type: str,
    confidence_score: float,
    assignee: Optional[str],
    current_user_name: Optional[str],
    target_user_id: Optional[str],
    current_user_id: Optional[str],
) -> Tuple[str, str, str]:
    """
    Returns (priority, reason, explanation).

    reason       = short label for the priority level
    explanation  = human-readable sentence explaining the classification
    """
    if item_type == "action_item":
        # Direct assignment to session owner
        if target_user_id and current_user_id and target_user_id == current_user_id:
            return (
                "critical",
                "Assigned directly to you",
                "This task was explicitly assigned to you during the meeting",
            )
        # Assignment to session owner by name match
        if assignee and current_user_name and _names_match(assignee, current_user_name):
            return (
                "critical",
                "Assigned to you by name",
                f"{assignee} was directly named in the assignment",
            )
        # High confidence + deadline → important
        if confidence_score >= 0.88:
            return ("important", "High-confidence task", "Strong action commitment detected")
        return ("info", "General task", "Action item detected but with lower confidence")

    if item_type == "decision":
        if confidence_score >= 0.90:
            return (
                "important",
                "High-confidence strategic decision",
                "Explicit agreement or commitment with high confidence",
            )
        if confidence_score >= 0.80:
            return ("important", "Decision detected", "Clear decision language with good confidence")
        return ("info", "Minor decision", "Decision language detected with moderate confidence")

    if item_type == "mention":
        return ("important", "Direct mention", "Your name was mentioned in the meeting")

    return ("info", "Background event", "")


def compute_confidence_score(
    confidence_label: str,
    source: str,
    has_deadline: bool = False,
    has_assignee: bool = False,
) -> float:
    """
    Compute a 0–1 confidence score.

    - "high" / "llm" starts at 0.88
    - "medium" starts at 0.70
    - "low" starts at 0.50
    - Has assignee +0.05
    - Has deadline +0.03
    - Detection method llm +0.04
    """
    base = {"high": 0.88, "medium": 0.70, "low": 0.50}.get(confidence_label, 0.65)
    if source == "llm":
        base = max(base, 0.88)
    if has_assignee:
        base = min(base + 0.05, 1.0)
    if has_deadline:
        base = min(base + 0.03, 1.0)
    return round(base, 2)


# ── Context window ─────────────────────────────────────────────────────────────

class ContextWindow:
    def __init__(self, maxlen: int = 12) -> None:
        self._buf: Deque[str] = deque(maxlen=maxlen)

    def push(self, text: str) -> None:
        self._buf.append(text.strip())

    @property
    def full(self) -> str:
        return " ".join(self._buf)


# ── Deduplicator ──────────────────────────────────────────────────────────────

class _Deduplicator:
    def __init__(self, ttl_s: int = 180) -> None:
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, text: str) -> bool:
        now = time.monotonic()
        self._seen = {k: t for k, t in self._seen.items() if now - t <= self._ttl}
        normalized = re.sub(r"[^\w\s]", "", text.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()[:80]
        key = hashlib.md5(normalized.encode()).hexdigest()[:12]
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


# ── Detection record ─────────────────────────────────────────────────────────

@dataclass
class DetectedItem:
    item_type:         str
    text:              str
    raw:               str
    confidence_label:  str   # "high" | "medium" | "low"
    assignee:          Optional[str] = None
    source:            str = "regex"   # "regex" | "llm"
    detection_reason:  str = ""         # why this pattern matched
    related_context:   str = ""         # the sentence that triggered it
    has_deadline:      bool = False


# ── Low-level detection ───────────────────────────────────────────────────────

def _phrase_around(match: re.Match, full_text: str, pre: int = 40, post: int = 100) -> str:
    start = max(0, match.start() - pre)
    end   = min(len(full_text), match.end() + post)
    return re.sub(r"\s+", " ", full_text[start:end]).strip()


def _has_deadline(text: str) -> bool:
    return bool(re.search(
        r"\bby\s+(?:tomorrow|end of day|eod|eow|end of week|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"next week|next month)\b", text, re.I,
    ))


def _extract_assignee(text: str, attendee_names: List[str]) -> Optional[str]:
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
            if re.search(rf"\bassign(?:ed)?\s+to\s+{re.escape(part)}\b", text_lower):
                return full_name
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

    for pattern, base_score, explanation in _ACTION_PATTERNS:
        for m in pattern.finditer(search):
            phrase = _phrase_around(m, search)
            key = phrase[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            assignee    = _extract_assignee(phrase, attendee_names or [])
            has_deadline = _has_deadline(phrase)
            results.append(DetectedItem(
                item_type        = "action_item",
                text             = phrase,
                raw              = text,
                confidence_label = "high" if base_score >= 0.90 else ("medium" if base_score >= 0.72 else "low"),
                assignee         = assignee,
                source           = "regex",
                detection_reason = explanation,
                related_context  = phrase,
                has_deadline     = has_deadline,
            ))
    return results


def detect_decisions(
    text: str,
    context: Optional[str] = None,
) -> List[DetectedItem]:
    search = context or text
    results: List[DetectedItem] = []
    seen: set = set()

    for pattern, base_score, explanation in _DECISION_PATTERNS:
        for m in pattern.finditer(search):
            phrase = _phrase_around(m, search, pre=20, post=120)
            key = phrase[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(DetectedItem(
                item_type        = "decision",
                text             = phrase,
                raw              = text,
                confidence_label = "high" if base_score >= 0.90 else ("medium" if base_score >= 0.80 else "low"),
                source           = "regex",
                detection_reason = explanation,
                related_context  = phrase,
            ))
    return results


def detect_confirmation(text: str) -> bool:
    return any(p.search(text) for p in _CONFIRMATION_PATTERNS)


# ── LLM scan ──────────────────────────────────────────────────────────────────

LLM_SCAN_INTERVAL_S  = 60
LLM_MIN_NEW_SEGMENTS = 5

_LLM_PROMPT = """\
You are a meeting intelligence assistant. For each action item or decision you find,
also explain in 1 sentence WHY it matters (reason) and assign a confidence score 0-1.

Return ONLY a JSON array. Each element must have:
  "type": "action_item" or "decision"
  "text": exact phrase under 120 chars
  "assignee": person name if mentioned, else null  (action_item only)
  "confidence": "high" or "medium" or "low"
  "detection_reason": one sentence explaining why this matters

If nothing found return [].

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
                item_type        = item["type"],
                text             = str(item.get("text", ""))[:200],
                raw              = "[llm scan]",
                confidence_label = item.get("confidence", "medium"),
                assignee         = item.get("assignee"),
                source           = "llm",
                detection_reason = item.get("detection_reason", "LLM-assessed high-value item"),
            ))
        return results
    except Exception as exc:
        logger.debug("[intelligence] llm_scan error: %s", exc)
        return []


# ── RealTimeIntelligence ─────────────────────────────────────────────────────

@dataclass
class _PendingActionItem:
    event_id:     str
    text:         str
    assignee:     Optional[str]
    target_uid:   Optional[str]
    detected_at:  float


class RealTimeIntelligence:
    """
    Per-meeting stateful intelligence processor.
    Extracted events carry full explanation + confidence metadata.
    Handles interrupt detection when target_user is absent.

    Constructor:
        meeting_id, attendee_names, attendee_map,
        current_user_id, current_user_name, organization_id,
        absent_user_ids (list of user_ids confirmed not in meeting)
    """

    def __init__(
        self,
        meeting_id: str,
        attendee_names: Optional[List[str]] = None,
        attendee_map=None,
        current_user_id: Optional[str] = None,
        current_user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
        absent_user_ids: Optional[List[str]] = None,
    ) -> None:
        self.meeting_id        = meeting_id
        self.attendee_names   = attendee_names or []
        self._attendee_map    = attendee_map
        self.current_user_id  = current_user_id
        self.current_user_name = current_user_name
        self.organization_id  = organization_id
        self._absent_uids     = set(absent_user_ids or [])

        self._context          = ContextWindow(maxlen=12)
        self._dedup_ai         = _Deduplicator(ttl_s=180)
        self._dedup_dec        = _Deduplicator(ttl_s=180)
        self._llm_buffer: List[str] = []
        self._last_llm_scan    = 0.0
        self._segments_since   = 0
        self._event_counter    = 0
        self._recent_pending: Deque[_PendingActionItem] = deque(maxlen=5)

    # ── Public API ────────────────────────────────────────────────────────────

    async def process(self, text: str, elapsed_s: float) -> List[dict]:
        """
        Process one transcript segment.
        Returns event dicts with full explanation + confidence metadata.
        Emits interrupt events for absent targets.
        """
        self._context.push(text)
        self._llm_buffer.append(text)
        self._segments_since += 1
        context = self._context.full
        events: List[dict] = []

        # ── Tier 1: regex ───────────────────────────────────────────────────
        for item in detect_action_items(text, self.attendee_names, context):
            if self._dedup_ai.is_duplicate(item.text):
                continue
            ev = self._build_event(item, elapsed_s)
            events.append(ev)
            self._recent_pending.append(_PendingActionItem(
                ev["event_id"], ev["text"], ev.get("assignee"), ev.get("target_user_id"),
                time.monotonic(),
            ))
            # Interrupt: assigned to someone absent
            interrupt_ev = self._check_interrupt(ev, elapsed_s)
            if interrupt_ev:
                events.append(interrupt_ev)
            asyncio.create_task(self._enrich_with_memory(ev["event_id"], item.text, "action_item"))

        for item in detect_decisions(text, context):
            if self._dedup_dec.is_duplicate(item.text):
                continue
            ev = self._build_event(item, elapsed_s)
            events.append(ev)
            asyncio.create_task(self._enrich_with_memory(ev["event_id"], item.text, "decision"))

        # Confirmation check
        if detect_confirmation(text):
            conf_ev = self._build_confirmation(text, elapsed_s)
            if conf_ev:
                events.append(conf_ev)

        # Tier 2: LLM scan
        now = time.monotonic()
        if (
            self._segments_since >= LLM_MIN_NEW_SEGMENTS
            and now - self._last_llm_scan >= LLM_SCAN_INTERVAL_S
        ):
            self._last_llm_scan = now
            self._segments_since = 0
            asyncio.create_task(self._run_llm_scan(elapsed_s))

        return events

    # ── Event building ────────────────────────────────────────────────────────

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"{self.meeting_id[:8]}:{self._event_counter}"

    def _resolve_target(self, assignee: Optional[str]) -> Optional[str]:
        if not assignee or not self._attendee_map:
            return None
        hit = self._attendee_map.resolve(assignee)
        return hit.user_id if hit else None

    def _build_event(self, item: DetectedItem, elapsed_s: float) -> dict:
        target_uid    = self._resolve_target(item.assignee)
        conf_score    = compute_confidence_score(
            item.confidence_label, item.source,
            has_deadline=item.has_deadline,
            has_assignee=bool(item.assignee),
        )
        priority, reason, explanation = compute_priority_and_reason(
            item.item_type, conf_score,
            item.assignee, self.current_user_name,
            target_uid, self.current_user_id,
        )
        event_id = self._next_event_id()

        ev: dict = {
            "type":              item.item_type,
            "event_id":         event_id,
            "text":             item.text,
            "assignee":         item.assignee,
            "target_user_id":   target_uid,
            "priority":         priority,
            "reason":           reason,
            "explanation":      explanation,
            "confidence_score": conf_score,
            "detection_method": item.source,
            "urgency_flag":     priority == "critical" and target_uid not in self._absent_uids,
            "related_context":   item.related_context,
            "confirmed":        False,
            "related_to_previous": None,
            "t":                round(elapsed_s, 1),
            "meeting_id":       self.meeting_id,
        }
        return ev

    def _check_interrupt(self, action_ev: dict, elapsed_s: float) -> Optional[dict]:
        """
        Fire an interrupt if this action item is assigned to someone absent
        from the meeting (and that person is the current user).
        """
        target_uid = action_ev.get("target_user_id")
        if not target_uid:
            return None

        is_absent   = target_uid in self._absent_uids
        is_current  = target_uid == self.current_user_id

        if is_absent and is_current:
            return {
                "type":          "interrupt",
                "event_id":      self._next_event_id(),
                "action_event_id": action_ev["event_id"],
                "action_text":   action_ev["text"],
                "assignee":      action_ev.get("assignee"),
                "target_user_id": target_uid,
                "reason":        "Assigned to you but you are not in the meeting",
                "explanation":   (
                    f"'{action_ev.get('assignee')}' assigned you a task "
                    f"at {format_time_elapsed(elapsed_s)} into the meeting. "
                    f"You have not joined yet."
                ),
                "urgency_flag":  True,
                "t":             round(elapsed_s, 1),
                "meeting_id":    self.meeting_id,
            }
        return None

    def _build_confirmation(self, text: str, elapsed_s: float) -> Optional[dict]:
        now = time.monotonic()
        for pending in reversed(self._recent_pending):
            if now - pending.detected_at <= 90:
                return {
                    "type":            "confirmation",
                    "event_id":        self._next_event_id(),
                    "action_event_id": pending.event_id,
                    "action_text":     pending.text,
                    "raw":             text.strip(),
                    "reason":          "Verbal confirmation",
                    "explanation":     "Someone confirmed this action during the meeting",
                    "confidence_score": 0.95,
                    "detection_method": "regex",
                    "urgency_flag":    False,
                    "t":               round(elapsed_s, 1),
                    "meeting_id":      self.meeting_id,
                }
        return None

    # ── Memory enrichment (background) ──────────────────────────────────────

    async def _enrich_with_memory(
        self, event_id: str, text: str, event_type: str
    ) -> None:
        if not self.organization_id:
            return
        try:
            from app.services.memory import find_related_async
            from app.core.redis import redis_client
            from datetime import datetime, timezone

            related = await find_related_async(
                text, event_type, self.meeting_id, self.organization_id,
            )
            if not related or not redis_client:
                return

            await redis_client.publish(
                f"live:{self.meeting_id}",
                json.dumps({
                    "type":               "event_enrichment",
                    "event_id":           event_id,
                    "original_type":      event_type,
                    "related_to_previous": related,
                    "meeting_id":         self.meeting_id,
                    "ts":                 datetime.now(timezone.utc).isoformat(),
                }),
            )
        except Exception as exc:
            logger.debug("[intelligence] memory enrichment error: %s", exc)

    async def _run_llm_scan(self, elapsed_s: float) -> None:
        try:
            from app.core.redis import redis_client
            from datetime import datetime, timezone

            transcript     = " ".join(self._llm_buffer[-30:])
            self._llm_buffer = self._llm_buffer[-10:]
            items = await llm_scan(transcript, self.attendee_names)

            for item in items:
                dedup = self._dedup_ai if item.item_type == "action_item" else self._dedup_dec
                if dedup.is_duplicate(item.text):
                    continue
                ev = self._build_event(item, elapsed_s)
                if redis_client:
                    ev.setdefault("ts", datetime.now(timezone.utc).isoformat())
                    await redis_client.publish(
                        f"live:{self.meeting_id}",
                        json.dumps(ev),
                    )
        except Exception as exc:
            logger.debug("[intelligence] llm scan task error: %s", exc)


# ── Helpers ──────────────────────────────────────────────────────────────────

def format_time_elapsed(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m{s}s" if m else f"{s}s"
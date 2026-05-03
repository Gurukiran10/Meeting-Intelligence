"""
Cross-meeting memory — lightweight keyword lookup in past meetings.

Algorithm (no vector store required):
  1. Extract the 4 most distinctive words from the event text
     (length > 5 chars, not meeting-domain stop words)
  2. Query action_items + decisions tables in the same org, past meetings
  3. Return the most recent match with meeting title + date

The lookup runs in a background executor thread so it never blocks the
real-time pipeline.  Results are published as a separate "event_enrichment"
event that the frontend merges into the original event by event_id.

Enrichment event shape:
  {
    "type":           "event_enrichment",
    "event_id":       "abc123:7",
    "original_type":  "action_item",
    "related_to_previous": {
      "meeting_id":    "...",
      "meeting_title": "Sprint planning",
      "meeting_date":  "2025-04-10",
      "text":          "We need to fix the dashboard performance",
      "event_type":    "action_item"
    }
  }
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Words that appear in almost every meeting — ignore for keyword matching
_STOP = frozenset({
    "meeting", "discussed", "decided", "action", "going", "should",
    "would", "about", "which", "there", "their", "these", "those",
    "think", "today", "tomorrow", "monday", "tuesday", "wednesday",
    "thursday", "friday", "everyone", "someone", "nothing", "something",
    "because", "before", "between", "through", "while", "since",
    "update", "check", "create", "review", "handle", "follow",
    "assign", "assigned", "deadline", "remind", "confirm",
})

# Only look back this far for past events
_LOOKBACK_DAYS = 90


def _extract_keywords(text: str) -> list[str]:
    """Return up to 4 distinctive words from text (len > 5, not stop words)."""
    words = re.findall(r"\b[a-zA-Z]{6,}\b", text.lower())
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.add(w)
            result.append(w)
            if len(result) == 4:
                break
    return result


def find_related_sync(
    text: str,
    event_type: str,          # "action_item" | "decision"
    current_meeting_id: str,
    organization_id: str,
) -> Optional[dict]:
    """
    Synchronous DB lookup — run via asyncio.get_event_loop().run_in_executor().
    Returns a dict with meeting context, or None if nothing relevant found.
    """
    keywords = _extract_keywords(text)
    if len(keywords) < 2:
        return None

    try:
        from app.core.database import SessionLocal
        from app.models.action_item import ActionItem
        from app.models.mention import Decision
        from app.models.meeting import Meeting
        from sqlalchemy import or_, and_

        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=_LOOKBACK_DAYS)

            # Build ILIKE conditions — require at least 2 keywords to match
            def _ilike_filters(column, kws):
                return [column.ilike(f"%{kw}%") for kw in kws]

            match = None

            if event_type in ("action_item", "both"):
                filters = _ilike_filters(ActionItem.extracted_from_text, keywords)
                # Use the first two as required (AND), rest as optional
                required = and_(filters[0], filters[1]) if len(filters) >= 2 else filters[0]
                row = (
                    db.query(ActionItem, Meeting)
                    .join(Meeting, ActionItem.meeting_id == Meeting.id)
                    .filter(
                        ActionItem.organization_id == organization_id,
                        ActionItem.meeting_id != current_meeting_id,
                        ActionItem.created_at >= cutoff,
                        required,
                    )
                    .order_by(ActionItem.created_at.desc())
                    .first()
                )
                if row:
                    ai, mtg = row
                    match = {
                        "meeting_id":    str(mtg.id),
                        "meeting_title": mtg.title,
                        "meeting_date":  (mtg.scheduled_start or ai.created_at).strftime("%Y-%m-%d"),
                        "text":          ai.title or (ai.extracted_from_text or "")[:120],
                        "event_type":    "action_item",
                    }

            if match is None and event_type in ("decision", "both"):
                filters = _ilike_filters(Decision.decision_text, keywords)
                required = and_(filters[0], filters[1]) if len(filters) >= 2 else filters[0]
                row = (
                    db.query(Decision, Meeting)
                    .join(Meeting, Decision.meeting_id == Meeting.id)
                    .filter(
                        Meeting.organization_id == organization_id,
                        Decision.meeting_id != current_meeting_id,
                        Meeting.scheduled_start >= cutoff,
                        required,
                    )
                    .order_by(Meeting.scheduled_start.desc())
                    .first()
                )
                if row:
                    dec, mtg = row
                    match = {
                        "meeting_id":    str(mtg.id),
                        "meeting_title": mtg.title,
                        "meeting_date":  (mtg.scheduled_start or datetime.utcnow()).strftime("%Y-%m-%d"),
                        "text":          (dec.decision_text or "")[:120],
                        "event_type":    "decision",
                    }

            return match
        finally:
            db.close()

    except Exception as exc:
        logger.debug("[memory] find_related_sync error (non-fatal): %s", exc)
        return None


async def find_related_async(
    text: str,
    event_type: str,
    current_meeting_id: str,
    organization_id: str,
) -> Optional[dict]:
    """Async wrapper — runs the sync DB query in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        find_related_sync,
        text,
        event_type,
        current_meeting_id,
        organization_id,
    )

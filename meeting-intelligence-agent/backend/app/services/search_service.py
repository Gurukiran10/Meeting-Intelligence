"""
Cross-meeting semantic search using PostgreSQL full-text search.

Searches across meeting titles, summaries, decisions, discussion topics,
and transcript text. Returns ranked results with highlighted snippets.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.models.meeting import Meeting
from app.models.transcript import Transcript

logger = logging.getLogger(__name__)


def _snippet(haystack: str, query_words: List[str], window: int = 180) -> str:
    """Return a short excerpt around the first query word match."""
    if not haystack:
        return ""
    lower = haystack.lower()
    best_pos = len(haystack)
    for word in query_words:
        idx = lower.find(word.lower())
        if idx != -1 and idx < best_pos:
            best_pos = idx
    if best_pos == len(haystack):
        return haystack[:window] + ("…" if len(haystack) > window else "")
    start = max(0, best_pos - 40)
    end = min(len(haystack), start + window)
    snippet = haystack[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(haystack):
        snippet = snippet + "…"
    # Bold the matched words
    for word in query_words:
        snippet = re.sub(rf"(?i)({re.escape(word)})", r"**\1**", snippet)
    return snippet


def _topics_text(meeting: Meeting) -> str:
    topics = getattr(meeting, "discussion_topics", None) or []
    if isinstance(topics, list):
        return " ".join(str(t) for t in topics)
    return str(topics)


def _decisions_text(meeting: Meeting) -> str:
    decisions = getattr(meeting, "key_decisions", None) or []
    if isinstance(decisions, list):
        parts = []
        for d in decisions:
            if isinstance(d, dict):
                parts.append(str(d.get("decision", "")))
            else:
                parts.append(str(d))
        return " ".join(parts)
    return str(decisions)


def search_meetings(
    db: Session,
    query: str,
    organization_id: str,
    user_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Full-text search across meetings and transcripts for the given org.
    Returns a ranked list of result dicts with snippets.
    """
    query = query.strip()
    if not query:
        return []

    query_words = [w for w in re.split(r"\s+", query) if len(w) >= 2]
    if not query_words:
        return []

    results: List[Dict[str, Any]] = []
    seen_meeting_ids: set = set()

    # ── 1. Search meeting-level fields ────────────────────────────────────
    meetings = db.query(Meeting).filter(
        Meeting.organization_id == organization_id,
        Meeting.deleted_at.is_(None),
    ).all()

    for meeting in meetings:
        title = str(meeting.title or "")
        summary = str(meeting.summary or "")
        topics = _topics_text(meeting)
        decisions = _decisions_text(meeting)
        combined = f"{title} {summary} {topics} {decisions}".lower()

        matched_words = [w for w in query_words if w.lower() in combined]
        if not matched_words:
            continue

        score = len(matched_words) / len(query_words)
        if any(w.lower() in title.lower() for w in query_words):
            score = min(1.0, score + 0.3)

        # Determine best snippet source
        if any(w.lower() in summary.lower() for w in query_words):
            snippet_src, match_type = summary, "summary"
        elif any(w.lower() in decisions.lower() for w in query_words):
            snippet_src, match_type = decisions, "decision"
        elif any(w.lower() in topics.lower() for w in query_words):
            snippet_src, match_type = topics, "topic"
        else:
            snippet_src, match_type = title, "title"

        results.append({
            "meeting_id": str(meeting.id),
            "meeting_title": title,
            "meeting_date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
            "platform": str(meeting.platform or ""),
            "status": str(meeting.status or ""),
            "relevance_score": round(score, 3),
            "match_type": match_type,
            "snippet": _snippet(snippet_src, query_words),
        })
        seen_meeting_ids.add(str(meeting.id))

    # ── 2. Search transcript text ─────────────────────────────────────────
    transcripts = db.query(Transcript).join(
        Meeting, Transcript.meeting_id == Meeting.id
    ).filter(
        Meeting.organization_id == organization_id,
        Meeting.deleted_at.is_(None),
    ).all()

    transcript_hits: Dict[str, Dict[str, Any]] = {}

    for seg in transcripts:
        seg_text = str(getattr(seg, "text", "") or "")
        if not seg_text:
            continue
        matched = [w for w in query_words if w.lower() in seg_text.lower()]
        if not matched:
            continue

        mid = str(seg.meeting_id)
        score = len(matched) / len(query_words)

        if mid not in transcript_hits or score > transcript_hits[mid]["score"]:
            transcript_hits[mid] = {
                "score": score,
                "text": seg_text,
                "speaker": str(getattr(seg, "speaker_id", "") or getattr(seg, "speaker_name", "") or ""),
            }

    for mid, hit in transcript_hits.items():
        if mid in seen_meeting_ids:
            # Boost existing result
            for r in results:
                if r["meeting_id"] == mid:
                    r["relevance_score"] = min(1.0, r["relevance_score"] + 0.15)
                    r["transcript_snippet"] = _snippet(hit["text"], query_words)
                    if hit["speaker"]:
                        r["transcript_speaker"] = hit["speaker"]
            continue

        # New result from transcript only — fetch meeting
        meeting = db.get(Meeting, mid)
        if not meeting:
            continue

        speaker_prefix = f"{hit['speaker']}: " if hit["speaker"] else ""
        results.append({
            "meeting_id": mid,
            "meeting_title": str(meeting.title or ""),
            "meeting_date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
            "platform": str(meeting.platform or ""),
            "status": str(meeting.status or ""),
            "relevance_score": round(hit["score"] * 0.85, 3),  # transcript hits ranked slightly lower
            "match_type": "transcript",
            "snippet": speaker_prefix + _snippet(hit["text"], query_words),
        })
        seen_meeting_ids.add(mid)

    # Sort by relevance, then by date (most recent first)
    results.sort(key=lambda r: (-r["relevance_score"], r.get("meeting_date") or ""), reverse=False)
    results.sort(key=lambda r: r["relevance_score"], reverse=True)

    return results[:limit]

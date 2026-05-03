"""Decision & Context Linking — cross-meeting decision timeline and institutional memory."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.models.meeting import Meeting
from app.models.mention import Mention


def _decision_text(d: Any) -> str:
    if isinstance(d, dict):
        return str(d.get("decision") or d.get("text") or "").strip()
    return str(d or "").strip()


def _normalize(text: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is", "are", "was", "we", "will", "by"}
    return {w.lower().strip(".,!?:;-'\"") for w in text.split() if len(w) > 2 and w.lower() not in stop}


def _overlap(s1: set[str], s2: set[str], min_shared: int = 2) -> bool:
    return len(s1 & s2) >= min_shared


def get_decision_graph(db: Session, organization_id: str, days: int = 90) -> Dict[str, Any]:
    """
    Returns a cross-meeting decision timeline with evolution grouping.

    Each group = a cluster of related decisions across meetings, showing
    how a topic was decided, revisited, or reversed over time.
    """
    since = datetime.utcnow() - timedelta(days=days)

    meetings = db.execute(
        select(Meeting).where(
            Meeting.organization_id == organization_id,
            Meeting.scheduled_start >= since,
            Meeting.deleted_at.is_(None),
        ).order_by(Meeting.scheduled_start)
    ).scalars().all()

    # Flat list of all decisions with meeting context
    all_decisions: List[Dict[str, Any]] = []
    for meeting in meetings:
        raw_decisions = getattr(meeting, "key_decisions", None) or []
        for d in raw_decisions:
            text = _decision_text(d)
            if not text:
                continue
            reasoning = d.get("reasoning", "") if isinstance(d, dict) else ""
            all_decisions.append({
                "meeting_id": str(meeting.id),
                "meeting_title": meeting.title,
                "date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
                "decision": text,
                "reasoning": reasoning,
                "tokens": _normalize(text),
            })

    # Also pull from mentions marked as decisions
    mentions = db.execute(
        select(Mention).where(
            Mention.meeting_id.in_([m.id for m in meetings]),
            or_(Mention.is_decision == True, Mention.mention_type == "decision_impact"),
        )
    ).scalars().all()

    meeting_map = {str(m.id): m for m in meetings}
    for mention in mentions:
        text = str(mention.mentioned_text or "").strip()
        if not text:
            continue
        m = meeting_map.get(str(mention.meeting_id))
        all_decisions.append({
            "meeting_id": str(mention.meeting_id),
            "meeting_title": m.title if m else "",
            "date": mention.created_at.isoformat() if mention.created_at else None,
            "decision": text,
            "reasoning": str(mention.full_context or ""),
            "tokens": _normalize(text),
            "source": "mention",
        })

    # Group related decisions
    groups: List[List[Dict]] = []
    used = [False] * len(all_decisions)
    for i, d1 in enumerate(all_decisions):
        if used[i]:
            continue
        group = [d1]
        used[i] = True
        for j, d2 in enumerate(all_decisions[i + 1:], start=i + 1):
            if not used[j] and _overlap(d1["tokens"], d2["tokens"]):
                group.append(d2)
                used[j] = True
        groups.append(group)

    # Build evolution threads
    threads = []
    for group in groups:
        sorted_group = sorted(group, key=lambda x: x.get("date") or "")
        first = sorted_group[0]
        latest = sorted_group[-1]
        revisited = len(sorted_group) > 1
        threads.append({
            "topic": first["decision"][:120],
            "first_decided": first["date"],
            "last_seen": latest["date"],
            "occurrences": len(sorted_group),
            "revisited": revisited,
            "status": "revisited" if revisited else "decided",
            "meetings": [
                {"meeting_id": d["meeting_id"], "meeting_title": d["meeting_title"], "date": d["date"], "decision": d["decision"], "reasoning": d.get("reasoning", "")}
                for d in sorted_group
            ],
        })

    threads.sort(key=lambda t: (t["occurrences"], t["last_seen"] or ""), reverse=True)

    return {
        "threads": threads,
        "total_decisions": len(all_decisions),
        "total_threads": len(threads),
        "revisited_count": sum(1 for t in threads if t["revisited"]),
        "meetings_analyzed": len(meetings),
        "days": days,
    }


def search_institutional_memory(db: Session, organization_id: str, query: str, days: int = 365) -> List[Dict[str, Any]]:
    """
    Search all decisions across meetings in the org for institutional memory.
    Returns ranked results with meeting context.
    """
    since = datetime.utcnow() - timedelta(days=days)
    query_lower = query.lower().strip()
    query_tokens = _normalize(query)

    meetings = db.execute(
        select(Meeting).where(
            Meeting.organization_id == organization_id,
            Meeting.scheduled_start >= since,
            Meeting.deleted_at.is_(None),
        )
    ).scalars().all()

    results: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for meeting in meetings:
        raw_decisions = getattr(meeting, "key_decisions", None) or []
        for d in raw_decisions:
            text = _decision_text(d)
            if not text:
                continue
            key = f"{meeting.id}:{text[:60]}"
            if key in seen:
                continue
            seen.add(key)

            decision_tokens = _normalize(text)
            shared = len(query_tokens & decision_tokens)
            exact = query_lower in text.lower()

            if not exact and shared == 0:
                continue

            score = (10 if exact else 0) + shared * 3
            reasoning = d.get("reasoning", "") if isinstance(d, dict) else ""
            results.append({
                "meeting_id": str(meeting.id),
                "meeting_title": meeting.title,
                "date": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
                "decision": text,
                "reasoning": reasoning,
                "relevance_score": score,
            })

    # Also search mentions
    mentions = db.execute(
        select(Mention).where(
            Mention.meeting_id.in_([m.id for m in meetings]),
            or_(Mention.is_decision == True, Mention.mention_type == "decision_impact"),
        )
    ).scalars().all()

    meeting_map = {str(m.id): m for m in meetings}
    for mention in mentions:
        text = str(mention.mentioned_text or "").strip()
        if not text:
            continue
        key = f"mention:{mention.id}"
        if key in seen:
            continue
        seen.add(key)

        decision_tokens = _normalize(text)
        shared = len(query_tokens & decision_tokens)
        exact = query_lower in text.lower()
        if not exact and shared == 0:
            continue

        m = meeting_map.get(str(mention.meeting_id))
        results.append({
            "meeting_id": str(mention.meeting_id),
            "meeting_title": m.title if m else "",
            "date": mention.created_at.isoformat() if mention.created_at else None,
            "decision": text,
            "reasoning": str(mention.full_context or ""),
            "relevance_score": (10 if exact else 0) + shared * 3,
            "source": "mention",
        })

    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results[:20]

"""Personalized mention detection and alert helpers."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.mention import Mention
from app.models.notification import Notification
from app.models.user import User
from app.services.notifications import create_notification
from app.services.ai.nlp import MentionDetection, nlp_service
from app.services.integrations.slack import slack_service
from app.services.slack_service import send_slack_dm

logger = logging.getLogger(__name__)


MENTION_ALERT_BASE_URL = "http://localhost:3000"


def _as_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r",|\n|;|\|", value) if part.strip()]
    return []


def _build_aliases(user: User) -> List[str]:
    full_name = _as_optional_str(user.full_name)
    username = _as_optional_str(user.username)
    email = _as_optional_str(user.email)
    candidates = [
        full_name,
        full_name.split()[0] if full_name else None,
        username,
        email.split("@")[0] if email else None,
    ]
    aliases: List[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text.lower() not in {alias.lower() for alias in aliases}:
            aliases.append(text)
    return aliases


def _build_projects(user: User) -> List[str]:
    preferences = _as_dict(user.preferences)
    projects = _safe_list(preferences.get("projects"))
    projects.extend(_safe_list(preferences.get("project_names")))
    return [item for item in projects if item]


def _build_keywords(user: User) -> List[str]:
    preferences = _as_dict(user.preferences)
    department = _as_optional_str(user.department)
    job_title = _as_optional_str(user.job_title)
    role = _as_optional_str(user.role)
    keywords: List[str] = []
    for group in [
        _build_projects(user),
        _safe_list(preferences.get("responsibilities")),
        _safe_list(preferences.get("areas_of_responsibility")),
        _safe_list(preferences.get("keywords")),
        _safe_list(preferences.get("teams")),
        _safe_list(preferences.get("team_names")),
        [department] if department else [],
        [job_title] if job_title else [],
        [role] if role else [],
    ]:
        for item in group:
            text = str(item or "").strip()
            if text and text.lower() not in {existing.lower() for existing in keywords}:
                keywords.append(text)
    return keywords


def build_mention_profiles(users: Iterable[User]) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for user in users:
        full_name = _as_optional_str(user.full_name) or "Unknown User"
        username = _as_optional_str(user.username)
        email = _as_optional_str(user.email)
        role = _as_optional_str(user.job_title) or _as_optional_str(user.role) or "member"
        profiles.append(
            {
                "id": str(user.id),
                "name": full_name,
                "username": username,
                "email": email,
                "role": role,
                "department": _as_optional_str(user.department),
                "job_title": _as_optional_str(user.job_title),
                "projects": _build_projects(user),
                "preferences": _as_dict(user.preferences),
                "aliases": _build_aliases(user),
                "keywords": _build_keywords(user),
            }
        )
    return profiles


def _match_user(detection: MentionDetection, users: Iterable[User]) -> Optional[User]:
    target = detection.user_name.strip().lower()
    if not target:
        return None

    for user in users:
        aliases = _build_aliases(user)
        if target in {alias.lower() for alias in aliases}:
            return user
    return None


def _calculate_urgency(detection: MentionDetection) -> float:
    urgency = max(float(detection.relevance_score) - 10.0, 10.0)
    if detection.mention_type == "action_assignment":
        urgency += 20.0
    if detection.mention_type == "decision_impact":
        urgency += 15.0
    if detection.is_question:
        urgency += 10.0
    return max(0.0, min(100.0, urgency))


def _meeting_url(meeting: Meeting) -> str:
    meeting_url = _as_optional_str(meeting.meeting_url)
    if meeting_url:
        return meeting_url
    return f"{MENTION_ALERT_BASE_URL}/meetings/{meeting.id}"


def _extract_due_date(text: str) -> Optional[str]:
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:,?\s+\w+\s+\d{1,2}(?:st|nd|rd|th)?)?\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _extract_dependency(text: str, keywords: List[str]) -> Optional[str]:
    dependency_match = re.search(r"\bdependency:?\s*([^,.!?]+)", text, re.IGNORECASE)
    if dependency_match:
        return dependency_match.group(1).strip()
    for keyword in keywords:
        if len(keyword.split()) >= 2 and re.search(rf"\b{re.escape(keyword.lower())}\b", text.lower()):
            return keyword
    return None


def _voice_confirmation_detected(text: str) -> bool:
    return bool(re.search(r"\b(i'll|i will|we'll own|sounds good|yes,? I'll|assigned to|I'll handle|I'll take|I can take)\b", text, re.IGNORECASE))


def _derive_status_text(detection: MentionDetection) -> str:
    if detection.mention_type == "action_assignment":
        return "Assigned"
    if detection.mention_type == "question":
        return "Needs response"
    if detection.mention_type == "feedback":
        return "Recognition"
    if detection.mention_type == "decision_impact":
        return "Impacted"
    if detection.mention_type == "resource_request":
        return "Resource requested"
    return "Mentioned"


def _find_related_action_item(db: Session, meeting: Meeting, user: User, detection: MentionDetection) -> Optional[ActionItem]:
    action_items = db.execute(
        select(ActionItem).where(
            ActionItem.meeting_id == meeting.id,
            ActionItem.owner_id == user.id,
        )
    ).scalars().all()
    if not action_items:
        return None

    detection_words = {word for word in re.findall(r"[a-zA-Z]{4,}", detection.text.lower())}
    best_item: Optional[ActionItem] = None
    best_score = -1
    for action_item in action_items:
        haystack = " ".join(
            part for part in [
                _as_optional_str(action_item.title),
                _as_optional_str(action_item.description),
                _as_optional_str(action_item.extracted_from_text),
            ] if part
        ).lower()
        score = sum(1 for word in detection_words if word in haystack)
        if score > best_score:
            best_score = score
            best_item = action_item
    return best_item


def _build_alert_details(db: Session, meeting: Meeting, user: User, detection: MentionDetection) -> Dict[str, Any]:
    related_action_item = _find_related_action_item(db, meeting, user, detection)
    source_text = " ".join(part for part in [detection.text, detection.context] if part)
    due_date = None
    owner = _as_optional_str(user.full_name) or _as_optional_str(user.username) or _as_optional_str(user.email)
    dependency = _extract_dependency(source_text, _build_keywords(user))
    action_item_id = None

    if related_action_item:
        action_item_id = str(related_action_item.id)
        action_due_date = getattr(related_action_item, "due_date", None)
        if action_due_date is not None:
            due_date = action_due_date.strftime("%A, %b %d")
        dependency = dependency or _as_optional_str(related_action_item.title)
        owner = owner or str(user.id)

    if not due_date:
        due_date = _extract_due_date(source_text)

    status = _derive_status_text(detection)
    if _voice_confirmation_detected(source_text):
        status = f"{status} (Voice Confirmation detected)"

    return {
        "action_item_id": action_item_id,
        "owner": owner,
        "dependency": dependency,
        "due_date": due_date,
        "status": status,
        "voice_confirmation_detected": _voice_confirmation_detected(source_text),
    }


async def detect_and_store_mentions(
    db: Session,
    meeting: Meeting,
    transcript_text: str,
    transcript_id: Optional[str] = None,
    candidate_users: Optional[List[User]] = None,
    send_real_time_alerts: bool = True,
    meeting_context: Optional[Dict[str, Any]] = None,
) -> List[Mention]:
    if not transcript_text.strip():
        return []

    users = candidate_users
    if users is None:
        users = db.execute(
            select(User).where(
                User.is_active.is_(True),
                User.organization_id == meeting.organization_id,
            )
        ).scalars().all()

    if not users:
        return []

    profiles = build_mention_profiles(users)
    detections = await nlp_service.detect_mentions(
        transcript_text,
        profiles,
        meeting_context={
            "meeting_title": meeting.title,
            "platform": meeting.platform,
            **(meeting_context or {}),
        },
    )

    created_mentions: List[Mention] = []
    meeting_link = _meeting_url(meeting)

    for detection in detections:
        matched_user = _match_user(detection, users)
        if not matched_user:
            continue

        existing = db.execute(
            select(Mention).where(
                Mention.meeting_id == meeting.id,
                Mention.user_id == matched_user.id,
                Mention.mentioned_text == detection.text[:1000],
            )
        ).scalar_one_or_none()
        if existing:
            continue

        alert_details = _build_alert_details(db, meeting, matched_user, detection)

        mention = Mention(
            organization_id=meeting.organization_id,
            meeting_id=meeting.id,
            user_id=matched_user.id,
            transcript_id=transcript_id,
            mention_type=detection.mention_type,
            mentioned_text=detection.text[:1000],
            full_context=detection.context[:2000],
            context_before=(detection.context[:900] if detection.context else None),
            context_after=None,
            is_action_item=bool(getattr(detection, 'is_action_item', False)),
            is_question=bool(getattr(detection, 'is_question', False)),
            is_decision=detection.mention_type in ["decision_impact", "decision"],
            is_feedback=detection.mention_type == "feedback",
            relevance_score=float(getattr(detection, 'relevance_score', 0)),
            urgency_score=_calculate_urgency(detection),
            sentiment=getattr(detection, 'sentiment', "neutral"),
            sentiment_score=getattr(detection, 'sentiment_score', None),
            detection_method=getattr(detection, 'detection_method', "personalized_heuristic"),
            confidence=max(min(float(getattr(detection, 'relevance_score', 0)) / 100.0, 1.0), 0.0),
            mention_metadata={
                "meeting_title": meeting.title,
                "platform": meeting.platform,
                "transcript_id": transcript_id,
                "meeting_url": meeting_link,
                "matched_alias": getattr(detection, 'matched_alias', None),
                "matched_keyword": getattr(detection, 'matched_keyword', None),
                "decision_signal": getattr(detection, 'decision_signal', None),
                **alert_details,
            },
        )
        db.add(mention)
        db.flush()

        # ── Build the rich notification message ─────────────────────────
        rich_message = (
            f"👋 You were mentioned in a meeting\n\n"
            f"📌 Meeting: {meeting.title}\n"
            f"📎 View: {meeting_link}\n"
        )
        task_title = alert_details.get("dependency")
        due_date_str = alert_details.get("due_date")
        if task_title:
            rich_message += f"\n📌 Task: {task_title}"
        if due_date_str:
            rich_message += f"\n⏰ Deadline: {due_date_str}"

        # ── Dedup: skip if a Phase 1 notification already exists ────────
        existing_notif = db.execute(
            select(Notification).where(
                Notification.user_id == matched_user.id,
                Notification.notification_metadata["meeting_id"].as_string() == str(meeting.id),
                Notification.type == "mention",
            )
        ).scalar_one_or_none()

        if not existing_notif:
            create_notification(
                db,
                user_id=matched_user.id,
                organization_id=meeting.organization_id,
                notification_type="mention",
                message=rich_message,
                notification_metadata={
                    "mention_id": str(mention.id),
                    "meeting_id": str(meeting.id),
                    "mention_type": detection.mention_type,
                    "source": "heuristic_detection",
                },
            )

        notification_settings = _as_dict(matched_user.notification_settings)
        slack_settings = dict((matched_user.integrations or {}).get("slack", {}))
        recipient_email = _as_optional_str(matched_user.email)
        confidence_threshold = float(notification_settings.get("mention_confidence_threshold", 0.7))
        alert_channels = notification_settings.get("alert_channels", ["slack", "email"])
        mention_confidence = float(getattr(mention, "confidence", 0.0))

        # Only send alert if confidence meets threshold
        if send_real_time_alerts and mention_confidence >= confidence_threshold:
            # ── Org-level Slack integration (rich block kit alert) ───────
            if (
                "slack" in alert_channels
                and notification_settings.get("slack_enabled", True)
                and notification_settings.get("real_time_mentions", True)
                and slack_settings.get("bot_token")
                and recipient_email
            ):
                try:
                    await slack_service.send_mention_alert_via_token(
                        bot_token=slack_settings["bot_token"],
                        recipient_email=recipient_email,
                        mention_data={
                            "id": str(mention.id),
                            "meeting_id": str(meeting.id),
                            "mention_type": detection.mention_type,
                            "text": detection.text,
                            "context": detection.context,
                            "relevance_score": detection.relevance_score,
                            "urgency_score": mention.urgency_score,
                            "is_action_item": detection.is_action_item,
                            "is_question": detection.is_question,
                            **alert_details,
                        },
                        meeting_title=str(meeting.title),
                        meeting_url=meeting_link,
                    )
                    setattr(mention, "notification_sent", True)
                    setattr(mention, "notification_sent_at", datetime.utcnow())
                    setattr(mention, "notification_type", "slack")
                except Exception as exc:
                    logger.warning("Failed to send org Slack mention alert for %s: %s", matched_user.email, exc)

            # ── Personal Slack DM via slack_user_id (simple path) ───────
            elif matched_user.slack_user_id:
                send_slack_dm(matched_user.slack_user_id, rich_message)

            # Email alert (placeholder for future implementation)
            if "email" in alert_channels and notification_settings.get("email_enabled", True) and recipient_email:
                # TODO: Implement email alert logic
                pass

        created_mentions.append(mention)

    if created_mentions:
        db.commit()
        for mention in created_mentions:
            db.refresh(mention)

    return created_mentions

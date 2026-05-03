"""Post-meeting summary and delivery helpers."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.mention import Mention
from app.models.transcript import Transcript
from app.models.user import User
from app.services.ai.nlp import ActionItem as NLPActionItem
from app.services.ai.nlp import Decision as NLPDecision
from app.services.ai.nlp import nlp_service
from app.services.integrations.slack import slack_service

logger = logging.getLogger(__name__)

from app.core.config import settings as _settings
APP_BASE_URL = _settings.FRONTEND_URL


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> Optional[str]:
    text = _text(value)
    return text or None


class PostMeetingSummaryService:
    """Generate post-meeting summaries, personalized views, and Slack delivery."""

    async def generate_summary_for_meeting(self, db: Session, meeting: Meeting) -> Dict[str, Any]:
        transcripts = list(
            db.execute(select(Transcript).where(Transcript.meeting_id == getattr(meeting, "id", None))).scalars().all()
        )
        if not transcripts:
            logger.warning("No transcripts found for meeting %s", getattr(meeting, "id", None))
            return {}

        full_transcript = "\n".join(_text(getattr(t, "text", "")) for t in transcripts if _text(getattr(t, "text", "")))
        participant_users = self._load_participant_users(db, meeting)
        attendee_names = [
            _text(getattr(user, "full_name", "")) or _text(getattr(user, "username", "")) or _text(getattr(user, "email", ""))
            for user in participant_users
        ]

        summary_data = await nlp_service.generate_summary(
            transcript=full_transcript,
            meeting_title=_text(getattr(meeting, "title", "Meeting")),
            attendees=attendee_names,
        )

        meeting.summary = summary_data.executive_summary  # type: ignore[attr-defined]
        meeting.key_decisions = [self._decision_to_dict(decision) for decision in summary_data.decisions]  # type: ignore[attr-defined]
        meeting.discussion_topics = list(summary_data.discussion_topics)  # type: ignore[attr-defined]
        meeting.sentiment_score = float(summary_data.sentiment_score)  # type: ignore[attr-defined]
        db.commit()

        mentions = list(
            db.execute(select(Mention).where(Mention.meeting_id == getattr(meeting, "id", None))).scalars().all()
        )
        existing_action_items = list(
            db.execute(select(ActionItem).where(ActionItem.meeting_id == getattr(meeting, "id", None))).scalars().all()
        )

        action_items = self._format_action_items(
            existing_action_items,
            fallback_items=list(summary_data.action_items),
        )

        return {
            "meeting_id": str(getattr(meeting, "id", "")),
            "meeting_title": _text(getattr(meeting, "title", "Meeting")),
            "meeting_date": (lambda d: d.isoformat() if d is not None else None)(getattr(meeting, "scheduled_start", None)),
            "duration_minutes": getattr(meeting, "duration_minutes", None),
            "attendee_count": getattr(meeting, "attendee_count", None) or len(participant_users),
            "executive_summary": summary_data.executive_summary,
            "key_decisions": [self._decision_to_dict(decision) for decision in summary_data.decisions],
            "action_items": action_items,
            "discussion_topics": list(summary_data.discussion_topics),
            "sentiment": {
                "overall": summary_data.sentiment,
                "score": float(summary_data.sentiment_score),
                "analysis": self._analyze_sentiment_patterns(transcripts),
            },
            "personalized_sections": self._generate_personalized_sections(participant_users, mentions, existing_action_items),
            "generated_at": datetime.utcnow().isoformat(),
            "processing_time_seconds": self._processing_seconds(meeting),
        }

    def _decision_to_dict(self, decision: NLPDecision) -> Dict[str, Any]:
        return {
            "decision": decision.decision,
            "reasoning": decision.reasoning,
            "decision_maker": decision.decision_maker,
            "alternatives_considered": list(decision.alternatives),
            "is_reversible": bool(decision.is_reversible),
            "impact_level": decision.impact_level,
        }

    def _format_action_items(
        self,
        stored_action_items: List[ActionItem],
        fallback_items: List[NLPActionItem],
    ) -> List[Dict[str, Any]]:
        if stored_action_items:
            formatted: List[Dict[str, Any]] = []
            for item in stored_action_items:
                due_date = getattr(item, "due_date", None)
                description = _text(getattr(item, "description", ""))
                metadata = dict(getattr(item, "item_metadata", None) or {})
                formatted.append(
                    {
                        "task": _text(getattr(item, "title", "Action Item")),
                        "owner": metadata.get("owner_name") or str(getattr(item, "owner_id", "") or "Unassigned"),
                        "deadline": due_date.isoformat() if due_date is not None else None,
                        "description": description,
                        "priority": _text(getattr(item, "priority", "medium")),
                        "urgency": self._calculate_urgency(_text(getattr(item, "priority", "medium")), due_date),
                        "context_dependencies": metadata.get("context_dependencies") or self._extract_dependencies(description),
                    }
                )
            return formatted

        return [
            {
                "task": item.title,
                "owner": item.owner,
                "deadline": item.due_date,
                "description": item.description,
                "priority": item.priority,
                "urgency": self._calculate_urgency(item.priority, None, item.due_date),
                "context_dependencies": self._extract_dependencies(item.description),
            }
            for item in fallback_items
        ]

    def _calculate_urgency(self, priority: str, due_date: Optional[datetime], due_date_text: Optional[str] = None) -> str:
        if priority == "urgent":
            return "high"
        if due_date is not None:
            days_until = (due_date - datetime.utcnow()).days
            if days_until <= 1:
                return "high"
            if days_until <= 3:
                return "medium"
        if due_date_text and any(keyword in due_date_text.lower() for keyword in ["today", "tomorrow", "asap", "urgent"]):
            return "high"
        return "low"

    def _extract_dependencies(self, description: str) -> List[str]:
        """Extract dependencies from action item description"""
        dependencies = []
        desc_lower = description.lower()

        # Look for common dependency indicators
        indicators = [
            "after", "before", "depends on", "once", "when", "following",
            "pending", "waiting for", "requires", "needs"
        ]

        for indicator in indicators:
            if indicator in desc_lower:
                # Simple extraction - could be enhanced with NLP
                start = desc_lower.find(indicator)
                if start >= 0:
                    dep_text = description[start:start+100].strip()
                    if len(dep_text) > len(indicator) + 5:
                        dependencies.append(dep_text)

        return list(set(dependencies))[:3]  # Limit to 3 unique dependencies

    def _analyze_sentiment_patterns(self, transcripts: List[Transcript]) -> Dict[str, Any]:
        if not transcripts:
            return {"energy": "neutral", "tension": "low", "participation_balance": "balanced"}

        # Simple analysis based on transcript patterns
        total_segments = len(transcripts)
        question_count = sum(1 for t in transcripts if "?" in t.text)
        interruption_indicators = sum(1 for t in transcripts if any(word in t.text.lower() for word in ["actually", "no", "wait", "hold on", "that's not right"]))

        # Participation balance (simplified)
        speaker_counts = {}
        for t in transcripts:
            speaker = t.speaker_name or f"speaker_{t.speaker_id}"
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1

        max_participation = max(speaker_counts.values()) if speaker_counts else 0
        total_participation = sum(speaker_counts.values())

        participation_balance = "balanced"
        average = (total_participation / len(speaker_counts)) if speaker_counts else 0
        if average and max_participation > 2 * average:
            participation_balance = "uneven"

        energy = "neutral"
        if question_count > total_segments * 0.3:
            energy = "engaged"
        elif interruption_indicators > total_segments * 0.1:
            energy = "tense"

        tension = "low"
        if interruption_indicators > total_segments * 0.15:
            tension = "high"
        elif interruption_indicators > total_segments * 0.05:
            tension = "medium"

        return {
            "energy": energy,
            "tension": tension,
            "participation_balance": participation_balance,
            "question_ratio": question_count / total_segments if total_segments > 0 else 0,
            "interruption_ratio": interruption_indicators / total_segments if total_segments > 0 else 0,
        }

    def _generate_personalized_sections(
        self,
        users: List[User],
        mentions: List[Mention],
        action_items: List[ActionItem],
    ) -> Dict[str, Dict[str, Any]]:
        personalized: Dict[str, Dict[str, Any]] = {}
        mentions_by_user: Dict[str, List[Mention]] = {}
        for mention in mentions:
            mentions_by_user.setdefault(str(getattr(mention, "user_id", "")), []).append(mention)

        for user in users:
            user_id = str(getattr(user, "id", ""))
            user_mentions = mentions_by_user.get(user_id, [])
            user_actions = [item for item in action_items if getattr(item, "owner_id", None) == getattr(user, "id", None)]
            decision_mentions = [
                mention for mention in user_mentions if _text(getattr(mention, "mention_type", "")) in {"decision_impact", "action_assignment"}
            ]

            personalized[user_id] = {
                "user_name": _text(getattr(user, "full_name", "")) or _text(getattr(user, "username", "")),
                "mentions_about_you": [
                    {
                        "type": _text(getattr(m, "mention_type", "")),
                        "text": _text(getattr(m, "mentioned_text", "")),
                        "context": _text(getattr(m, "full_context", "")),
                        "urgency": getattr(m, "urgency_score", None),
                    }
                    for m in user_mentions
                ],
                "actions_assigned": [
                    {
                        "task": _text(getattr(ai, "title", "")),
                        "deadline": (lambda d: d.isoformat() if d is not None else None)(getattr(ai, "due_date", None)),
                        "priority": _text(getattr(ai, "priority", "")),
                        "description": _text(getattr(ai, "description", "")),
                    }
                    for ai in user_actions
                ],
                "decisions_affecting_work": [
                    {
                        "decision": _text(getattr(m, "mentioned_text", "")),
                        "impact": _text(getattr(m, "mention_type", "")),
                        "context": _text(getattr(m, "full_context", "")),
                    }
                    for m in decision_mentions
                ],
            }
        return personalized

    async def send_summary_to_attendees(
        self,
        db: Session,
        meeting: Meeting,
        summary: Dict[str, Any],
    ) -> Dict[str, bool]:
        """Send personalized summaries to all attendees"""
        results: Dict[str, bool] = {}
        attendees = self._load_participant_users(db, meeting)

        for attendee in attendees:
            try:
                personalized_data = summary.get("personalized_sections", {}).get(str(getattr(attendee, "id", "")), {})
                success = await self._send_personalized_summary(
                    attendee, meeting, summary, personalized_data
                )
                results[str(getattr(attendee, "id", ""))] = success
            except Exception as exc:
                logger.error("Failed to send summary to %s: %s", getattr(attendee, "email", None), exc)
                results[str(getattr(attendee, "id", ""))] = False

        return results

    async def _send_personalized_summary(
        self,
        user: User,
        meeting: Meeting,
        summary: Dict[str, Any],
        personalized: Dict[str, Any],
    ) -> bool:
        """Send personalized summary to a user"""
        slack_settings = dict((getattr(user, "integrations", None) or {}).get("slack", {}))
        bot_token = _optional_text(slack_settings.get("bot_token"))
        recipient_email = _optional_text(getattr(user, "email", ""))
        if not bot_token or not recipient_email:
            logger.warning("No Slack integration for user %s", getattr(user, "email", None))
            return False

        try:
            blocks = self._build_summary_blocks(meeting, summary, personalized)
            await slack_service.send_blocks_via_token(
                bot_token=bot_token,
                recipient_email=recipient_email,
                text=f"Post-meeting summary: {_text(getattr(meeting, 'title', 'Meeting'))}",
                blocks=blocks,
            )
            return True
        except Exception as exc:
            logger.error("Failed to send summary to %s: %s", getattr(user, "email", None), exc)
            return False

    def _build_summary_blocks(
        self,
        meeting: Meeting,
        summary: Dict[str, Any],
        personalized: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Build Slack blocks for post-meeting summary"""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📋 Post-Meeting Summary: {_text(getattr(meeting, 'title', 'Meeting'))}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{summary.get('executive_summary', 'Summary unavailable')}*"},
            },
        ]

        # Key Decisions
        if summary.get("key_decisions"):
            decisions_text = "\n".join([
                f"• {d['decision']}" for d in summary["key_decisions"][:3]
            ])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Key Decisions:*\n{decisions_text}"},
            })

        # Action Items
        if summary.get("action_items"):
            actions_text = "\n".join([
                f"• {a['task']} - @{a['owner'] or 'Unassigned'} ({a['urgency']})"
                for a in summary["action_items"][:3]
            ])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Action Items:*\n{actions_text}"},
            })

        # Personalized section
        if personalized.get("actions_assigned"):
            your_actions = "\n".join([
                f"• {a['task']} (Due: {a['deadline'] or 'TBD'})"
                for a in personalized["actions_assigned"][:2]
            ])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Your Action Items:*\n{your_actions}"},
            })

        # Sentiment
        sentiment = summary.get("sentiment", {})
        analysis = sentiment.get("analysis", {}) if isinstance(sentiment, dict) else {}
        if isinstance(sentiment, dict) and sentiment.get("overall"):
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Meeting Energy: {str(sentiment['overall']).title()}"},
                    {"type": "mrkdwn", "text": f"Tension: {str(analysis.get('tension', 'low')).title()}"},
                ],
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Full Summary"},
                    "url": f"{APP_BASE_URL}/meetings/{getattr(meeting, 'id', '')}",
                    "style": "primary",
                }
            ],
        })

        return blocks

    def _load_participant_users(self, db: Session, meeting: Meeting) -> List[User]:
        users: List[User] = []
        seen: Set[str] = set()
        user_ids = list(getattr(meeting, "attendee_ids", None) or [])
        organizer_id = getattr(meeting, "organizer_id", None)
        if organizer_id is not None:
            user_ids.append(str(organizer_id))
        for user_id in user_ids:
            user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
            if user and str(getattr(user, "id", "")) not in seen:
                users.append(user)
                seen.add(str(getattr(user, "id", "")))
        return users

    def _processing_seconds(self, meeting: Meeting) -> float:
        finished_at = getattr(meeting, "actual_end", None) or getattr(meeting, "scheduled_end", None)
        if finished_at is None:
            return 0.0
        return max((datetime.utcnow() - finished_at).total_seconds(), 0.0)


post_meeting_summary_service = PostMeetingSummaryService()
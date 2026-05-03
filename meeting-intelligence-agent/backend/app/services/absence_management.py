"""Absence management and catch-up helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.mention import Mention
from app.models.transcript import Transcript
from app.models.user import User
from app.services.integrations.slack import slack_service

logger = logging.getLogger(__name__)

from app.core.config import settings as _settings
APP_BASE_URL = _settings.FRONTEND_URL


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> Optional[str]:
    text = _text(value)
    return text or None


class AbsenceManagementService:
    """Service for missed-meeting catch-up, prioritization, and async follow-up."""

    def find_absentees_for_meeting(self, db: Session, meeting: Meeting) -> List[User]:
        """Infer absentees from invited participants without transcript participation."""
        invited_ids = list(getattr(meeting, "attendee_ids", None) or [])
        organizer_id = getattr(meeting, "organizer_id", None)
        if organizer_id is not None:
            invited_ids.append(str(organizer_id))

        transcripts = list(
            db.execute(select(Transcript).where(Transcript.meeting_id == getattr(meeting, "id", None))).scalars().all()
        )
        participant_markers = {
            _text(getattr(t, "speaker_name", "")).lower()
            for t in transcripts
            if _text(getattr(t, "speaker_name", ""))
        }
        participant_ids = {
            str(getattr(t, "user_id", ""))
            for t in transcripts
            if getattr(t, "user_id", None) is not None
        }

        absentees: List[User] = []
        seen: Set[str] = set()
        for user_id in invited_ids:
            user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
            if not user:
                continue
            candidate_id = str(getattr(user, "id", ""))
            if candidate_id in seen:
                continue

            aliases = {
                _text(getattr(user, "full_name", "")).lower(),
                _text(getattr(user, "username", "")).lower(),
                _text(getattr(user, "email", "")).split("@")[0].lower(),
            }
            aliases.discard("")
            spoke = candidate_id in participant_ids or any(alias in participant_markers for alias in aliases)
            if not spoke:
                absentees.append(user)
                seen.add(candidate_id)
        return absentees

    async def generate_catch_up_for_absentee(self, db: Session, meeting: Meeting, absentee: User) -> Dict[str, Any]:
        transcripts = list(
            db.execute(select(Transcript).where(Transcript.meeting_id == getattr(meeting, "id", None))).scalars().all()
        )
        if not transcripts:
            logger.warning("No transcripts found for meeting %s", getattr(meeting, "id", None))
            return {}

        full_transcript = "\n".join(_text(getattr(t, "text", "")) for t in transcripts if _text(getattr(t, "text", "")))
        user_mentions = list(
            db.execute(
                select(Mention).where(
                    and_(
                        Mention.meeting_id == getattr(meeting, "id", None),
                        Mention.user_id == getattr(absentee, "id", None),
                    )
                )
            ).scalars().all()
        )
        user_actions = list(
            db.execute(
                select(ActionItem).where(
                    and_(
                        ActionItem.meeting_id == getattr(meeting, "id", None),
                        ActionItem.owner_id == getattr(absentee, "id", None),
                    )
                )
            ).scalars().all()
        )
        team_actions = list(
            db.execute(select(ActionItem).where(ActionItem.meeting_id == getattr(meeting, "id", None))).scalars().all()
        )
        questions_about_user = self._find_questions_about_user(full_transcript, absentee)
        prioritization = self._generate_smart_prioritization(user_mentions, user_actions, questions_about_user, team_actions)

        return {
            "meeting_id": str(getattr(meeting, "id", "")),
            "meeting_title": _text(getattr(meeting, "title", "Meeting")),
            "meeting_date": (lambda d: d.isoformat() if d is not None else None)(getattr(meeting, "scheduled_start", None)),
            "duration_minutes": getattr(meeting, "duration_minutes", None),
            "attendee_count": getattr(meeting, "attendee_count", None),
            "full_transcript": full_transcript,
            "personalized_highlights": self._generate_personalized_highlights(user_mentions),
            "decisions_affecting_work": self._find_decisions_affecting_user(user_mentions),
            "actions_assigned": [
                {
                    "task": _text(getattr(action, "title", "")),
                    "description": _text(getattr(action, "description", "")),
                    "deadline": (lambda d: d.isoformat() if d is not None else None)(getattr(action, "due_date", None)),
                    "priority": _text(getattr(action, "priority", "medium")),
                    "urgency": self._calculate_action_urgency(action),
                }
                for action in user_actions
            ],
            "questions_about_projects": questions_about_user,
            "smart_prioritization": prioritization,
            "async_participation": self._build_async_participation_options(meeting),
            "skip_recommendation": self._generate_skip_recommendation(meeting, user_mentions, user_actions, questions_about_user),
            "generated_at": datetime.utcnow().isoformat(),
        }

    def _generate_personalized_highlights(self, mentions: List[Mention]) -> Dict[str, Any]:
        if not mentions:
            return {"mention_count": 0, "highlights": []}

        highlights: List[Dict[str, Any]] = []
        for mention in mentions:
            highlights.append({
                "type": _text(getattr(mention, "mention_type", "")),
                "text": _text(getattr(mention, "mentioned_text", "")),
                "context": _text(getattr(mention, "full_context", "")),
                "urgency_score": getattr(mention, "urgency_score", None),
            })

        return {
            "mention_count": len(mentions),
            "highlights": highlights,
            "summary": f"Mentioned {len(mentions)} times — here is the context.",
        }

    def _find_decisions_affecting_user(self, mentions: List[Mention]) -> List[Dict[str, Any]]:
        """Find decisions that affect the user's work"""
        decisions = []

        for mention in mentions:
            if mention.mention_type in ["decision_impact", "action_assignment"]:
                decisions.append({
                    "decision": _text(getattr(mention, "mentioned_text", "")),
                    "impact": _text(getattr(mention, "mention_type", "")),
                    "context": _text(getattr(mention, "full_context", "")),
                    "urgency": getattr(mention, "urgency_score", None),
                })

        return decisions

    def _find_questions_about_user(self, transcript: str, user: User) -> List[Dict[str, Any]]:
        """Find questions asked about the user's projects or work"""
        questions = []
        sentences = transcript.split('.')

        user_identifiers = [
            _text(getattr(user, "full_name", "")).lower(),
            _text(getattr(user, "username", "")).lower(),
            _text(getattr(user, "email", "")).split('@')[0].lower(),
        ]
        user_identifiers = [identifier for identifier in user_identifiers if identifier]

        for sentence in sentences:
            sentence_lower = sentence.lower().strip()
            if '?' in sentence and any(identifier in sentence_lower for identifier in user_identifiers):
                questions.append({
                    "question": sentence.strip(),
                    "context": sentence.strip(),
                    "requires_response": True,
                })

        return questions

    def _generate_smart_prioritization(
        self,
        mentions: List[Mention],
        user_actions: List[ActionItem],
        questions: List[Dict[str, Any]],
        team_actions: List[ActionItem],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Generate smart prioritization of catch-up items"""

        critical = []
        important = []
        fyi = []

        for action in user_actions:
            due_date = getattr(action, "due_date", None)
            if _text(getattr(action, "priority", "")).lower() == "urgent" or (due_date is not None and due_date <= datetime.utcnow() + timedelta(hours=24)):
                critical.append({
                    "type": "action_assigned",
                    "title": _text(getattr(action, "title", "")),
                    "reason": f"Urgent action assigned to you - {_text(getattr(action, 'description', ''))}",
                    "deadline": due_date.isoformat() if due_date is not None else None,
                })

        # Critical: Questions requiring response
        for question in questions:
            critical.append({
                "type": "question",
                "title": "Question about your work",
                "reason": question["question"],
                "action_required": "Respond via Slack or email",
            })

        # Important: Decisions affecting work
        decision_mentions = [m for m in mentions if _text(getattr(m, "mention_type", "")) == "decision_impact"]
        for mention in decision_mentions:
            urgency_score = getattr(mention, "urgency_score", None)
            if urgency_score is not None and urgency_score >= 70:
                important.append({
                    "type": "decision_impact",
                    "title": "Decision affecting your work",
                    "reason": _text(getattr(mention, "mentioned_text", "")),
                    "action_required": "Review and provide input if needed",
                })

        # FYI: General mentions and team actions
        general_mentions = [m for m in mentions if m.mention_type not in ["decision_impact", "action_assignment"]]
        for mention in general_mentions:
            fyi.append({
                "type": "general_mention",
                "title": f"Mentioned in context of {_text(getattr(mention, 'mention_type', 'context'))}",
                "reason": _text(getattr(mention, "mentioned_text", "")),
                "action_required": "Read for awareness",
            })

        # FYI: Team actions (not assigned to user)
        user_action_ids = {getattr(a, "id", None) for a in user_actions}
        for action in team_actions:
            if getattr(action, "id", None) not in user_action_ids:
                fyi.append({
                    "type": "team_action",
                    "title": f"Team action: {_text(getattr(action, 'title', ''))}",
                    "reason": _text(getattr(action, "description", "")) or "Team commitment",
                    "action_required": "Monitor progress",
                })

        return {
            "critical": critical,
            "important": important,
            "fyi": fyi,
        }

    def _calculate_action_urgency(self, action: ActionItem) -> str:
        """Calculate urgency level for an action"""
        if _text(getattr(action, "priority", "")).lower() == "urgent":
            return "high"
        due_date = getattr(action, "due_date", None)
        if due_date is not None:
            days_until = (due_date - datetime.utcnow()).days
            if days_until <= 1:
                return "high"
            elif days_until <= 3:
                return "medium"
        return "low"

    def _generate_skip_recommendation(
        self,
        meeting: Meeting,
        mentions: List[Mention],
        user_actions: List[ActionItem],
        questions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Generate recommendation on whether this meeting was skippable"""

        # Analyze meeting content to determine if it was critical
        has_budget_decisions = any("budget" in _text(getattr(m, "mentioned_text", "")).lower() for m in mentions)
        has_urgent_actions = any(_text(getattr(a, "priority", "")).lower() == "urgent" for a in user_actions)
        has_questions = bool(questions)
        mention_count = len(mentions)
        action_count = len(user_actions)

        # Scoring logic
        score = 0
        reasons = []

        if has_budget_decisions:
            score += 10
            reasons.append("Budget decisions made")
        if has_urgent_actions:
            score += 8
            reasons.append("Urgent actions assigned")
        if has_questions:
            score += 6
            reasons.append("Questions about your work")
        if mention_count >= 5:
            score += 5
            reasons.append(f"Mentioned {mention_count} times")
        if action_count >= 2:
            score += 4
            reasons.append(f"{action_count} actions assigned")

        meeting_type = _text(getattr(meeting, "meeting_type", "")).lower()
        title = _text(getattr(meeting, "title", "")).lower()
        if score >= 15 or "approval" in title or "budget" in title:
            recommendation = "should_attend"
            message = "This meeting contained critical decisions and actions that required your presence."
        elif score >= 8 or meeting_type in {"review", "client"}:
            recommendation = "consider_attending"
            message = "This meeting had important elements that may have benefited from your input."
        else:
            recommendation = "safe_to_skip"
            message = "This meeting appears to have been primarily status updates and information sharing."

        return {
            "recommendation": recommendation,
            "message": message,
            "reasons": reasons,
            "score": score,
        }

    def _build_async_participation_options(self, meeting: Meeting) -> Dict[str, str]:
        meeting_id = str(getattr(meeting, "id", ""))
        return {
            "comment_url": f"{APP_BASE_URL}/meetings/{meeting_id}/respond?type=comment",
            "respond_url": f"{APP_BASE_URL}/meetings/{meeting_id}/respond?type=answer",
            "approve_url": f"{APP_BASE_URL}/meetings/{meeting_id}/respond?type=approve",
            "object_url": f"{APP_BASE_URL}/meetings/{meeting_id}/respond?type=object",
        }

    async def send_catch_up_to_absentee(
        self,
        db: Session,
        meeting: Meeting,
        absentee: User,
        catch_up_data: Dict[str, Any],
    ) -> bool:
        slack_settings = dict((getattr(absentee, "integrations", None) or {}).get("slack", {}))
        bot_token = _optional_text(slack_settings.get("bot_token"))
        recipient_email = _optional_text(getattr(absentee, "email", ""))
        if not bot_token or not recipient_email:
            logger.warning("No Slack integration for user %s", getattr(absentee, "email", None))
            return False

        try:
            blocks = self._build_catch_up_blocks(meeting, catch_up_data)
            await slack_service.send_blocks_via_token(
                bot_token=bot_token,
                recipient_email=recipient_email,
                text=f"Catch-up: {_text(getattr(meeting, 'title', 'Meeting'))}",
                blocks=blocks,
            )
            return True
        except Exception as exc:
            logger.error("Failed to send catch-up to %s: %s", getattr(absentee, "email", None), exc)
            return False

    def _build_catch_up_blocks(self, meeting: Meeting, catch_up: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build Slack blocks for catch-up package"""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📋 Catch-Up: {_text(getattr(meeting, 'title', 'Meeting'))}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"You missed this meeting. Here's what you need to know:"},
            },
        ]

        # Skip recommendation
        skip_rec = catch_up.get("skip_recommendation", {})
        if skip_rec.get("recommendation") == "safe_to_skip":
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"✅ *Safe to skip next time*\n{skip_rec.get('message', '')}"},
            })
        elif skip_rec.get("recommendation") == "should_attend":
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"⚠️ *Should attend next time*\n{skip_rec.get('message', '')}"},
            })

        # Personalized highlights
        highlights = catch_up.get("personalized_highlights", {})
        if highlights.get("mention_count", 0) > 0:
            mention_text = f"You were mentioned {highlights['mention_count']} times during this meeting."
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"👤 *Personal Mentions*\n{mention_text}"},
            })

        # Critical items
        prioritization = catch_up.get("smart_prioritization", {})
        critical_items = prioritization.get("critical", [])
        if critical_items:
            critical_text = "\n".join([f"• {item['reason']}" for item in critical_items[:3]])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"🚨 *Critical Items (Immediate Action)*\n{critical_text}"},
            })

        # Actions assigned
        actions = catch_up.get("actions_assigned", [])
        if actions:
            action_text = "\n".join([f"• {a['task']} (Due: {a['deadline'] or 'TBD'})" for a in actions[:3]])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"📝 *Actions Assigned to You*\n{action_text}"},
            })

        # Questions
        questions = catch_up.get("questions_about_projects", [])
        if questions:
            question_text = "\n".join([f"• {q['question']}" for q in questions[:2]])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"❓ *Questions About Your Work*\n{question_text}"},
            })

        # View full details button
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Full Catch-Up"},
                    "url": f"{APP_BASE_URL}/meetings/{getattr(meeting, 'id', '')}/catch-up",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Async Response"},
                    "url": f"{APP_BASE_URL}/meetings/{getattr(meeting, 'id', '')}/respond",
                    "style": "default",
                }
            ],
        })

        return blocks

    async def enable_async_participation(self, db: Session, meeting: Meeting, absentee: User) -> bool:
        meeting_metadata = dict(getattr(meeting, "meeting_metadata", None) or {})
        async_access = list(meeting_metadata.get("async_participants") or [])
        absentee_id = str(getattr(absentee, "id", ""))
        if absentee_id not in async_access:
            async_access.append(absentee_id)
            meeting_metadata["async_participants"] = async_access
            meeting.meeting_metadata = meeting_metadata  # type: ignore[attr-defined]
            db.commit()
        return True


    async def generate_catchup_for_user(self, db: Session, meeting: Meeting, user: User) -> Dict[str, Any]:
        """Wrapper used by the meetings API endpoint."""
        return await self.generate_catch_up_for_absentee(db, meeting, user)

    async def send_catchup_to_user(self, db: Session, meeting: Meeting, user: User) -> bool:
        """Generate and send catch-up to the user via Slack."""
        catch_up = await self.generate_catch_up_for_absentee(db, meeting, user)
        if not catch_up:
            return False
        return await self.send_catch_up_to_absentee(db, meeting, user, catch_up)


absence_management_service = AbsenceManagementService()
"""
AI Services - NLP Analysis Service
"""
import logging
from typing import List, Dict, Optional, Any, Tuple
import asyncio
import json
import re
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# Optional AI dependencies
try:
    from openai import AsyncOpenAI  # type: ignore
    GROK_CLIENT_AVAILABLE = True
except ImportError:
    logger.warning("OpenAI-compatible client not available. Grok NLP features will be limited.")
    GROK_CLIENT_AVAILABLE = False
    AsyncOpenAI = None

try:
    from anthropic import AsyncAnthropic  # type: ignore
    ANTHROPIC_AVAILABLE = True
except ImportError:
    logger.warning("Anthropic client not installed.")
    ANTHROPIC_AVAILABLE = False
    AsyncAnthropic = None

# Groq uses the same OpenAI-compatible SDK
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class MentionDetection(BaseModel):
    """Detected mention"""
    user_name: str
    mention_type: str  # direct, contextual, action_assignment, question, feedback, decision_impact, resource_request
    text: str
    context: str
    relevance_score: float
    is_action_item: bool = False
    is_question: bool = False


class ActionItem(BaseModel):
    """Extracted action item"""
    title: str
    description: str
    owner: Optional[str]
    due_date: Optional[str]
    priority: str  # low, medium, high, urgent
    confidence: float


class Decision(BaseModel):
    """Extracted decision"""
    decision: str
    reasoning: str
    alternatives: List[str]
    decision_maker: Optional[str]
    is_reversible: bool
    impact_level: str  # low, medium, high, critical


class MeetingSummary(BaseModel):
    """Meeting summary"""
    executive_summary: str
    key_points: List[str]
    decisions: List[Decision]
    action_items: List[ActionItem]
    discussion_topics: List[str]
    sentiment: str  # positive, negative, neutral
    sentiment_score: float


class NLPService:
    """Natural Language Processing Service"""
    
    def __init__(self):
        self.grok_client = (
            AsyncOpenAI(api_key=settings.GROK_API_KEY, base_url=settings.GROK_BASE_URL)
            if (settings.GROK_API_KEY and AsyncOpenAI is not None)
            else None
        )  # type: ignore
        self.anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY) if (settings.ANTHROPIC_API_KEY and AsyncAnthropic is not None) else None  # type: ignore
        self.groq_client = (
            AsyncOpenAI(api_key=settings.GROQ_API_KEY, base_url=GROQ_BASE_URL)
            if (settings.GROQ_API_KEY and AsyncOpenAI is not None)
            else None
        )  # type: ignore

    def _safe_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in re.split(r",|\n|;|\|", value) if part.strip()]
        return []

    def _normalize_user_profile(self, user_profile: Dict[str, Any]) -> Dict[str, Any]:
        preferences = user_profile.get("preferences") or {}
        name = str(user_profile.get("name") or "").strip()
        username = str(user_profile.get("username") or "").strip()
        email = str(user_profile.get("email") or "").strip()
        first_name = name.split()[0] if name else ""

        aliases = []
        for candidate in [name, first_name, username, email.split("@")[0] if email else ""]:
            candidate = str(candidate).strip()
            if candidate and candidate.lower() not in {alias.lower() for alias in aliases}:
                aliases.append(candidate)

        projects = self._safe_list(user_profile.get("projects"))
        projects.extend(self._safe_list(preferences.get("projects")))
        projects.extend(self._safe_list(preferences.get("project_names")))

        responsibilities = self._safe_list(preferences.get("responsibilities"))
        responsibilities.extend(self._safe_list(preferences.get("areas_of_responsibility")))
        responsibilities.extend(self._safe_list(preferences.get("keywords")))

        teams = self._safe_list(preferences.get("teams"))
        teams.extend(self._safe_list(preferences.get("team_names")))
        if user_profile.get("department"):
            teams.append(str(user_profile.get("department")))

        role_terms = self._safe_list([user_profile.get("role"), user_profile.get("job_title")])

        keywords: List[str] = []
        for group in [projects, responsibilities, teams, role_terms]:
            for item in group:
                item = str(item).strip()
                if item and item.lower() not in {existing.lower() for existing in keywords}:
                    keywords.append(item)

        return {
            **user_profile,
            "name": name,
            "aliases": aliases,
            "keywords": keywords,
            "projects": projects,
            "responsibilities": responsibilities,
            "teams": teams,
        }

    def _split_sentences(self, transcript: str) -> List[str]:
        parts = re.split(r"(?<=[.!?])\s+|\n+", transcript)
        return [part.strip() for part in parts if part and part.strip()]

    def _classify_sentence_for_user(self, sentence: str, normalized_user: Dict[str, Any]) -> Optional[Tuple[str, float, bool, bool, Dict[str, Any]]]:
        lowered = sentence.lower()
        aliases = [alias.lower() for alias in normalized_user.get("aliases", []) if alias]
        keywords = [keyword.lower() for keyword in normalized_user.get("keywords", []) if keyword]

        direct_alias = next((alias for alias in aliases if re.search(rf"\b{re.escape(alias)}\b", lowered)), None)
        keyword_hit = next((keyword for keyword in keywords if re.search(rf"\b{re.escape(keyword)}\b", lowered)), None)

        action_signal = bool(re.search(r"\b(can you|could you|please|need to|needs to|take on|handle|follow up|own|assigned to|let's have|will take|todo|action item)\b", lowered))
        decision_signal = bool(re.search(r"\b(decided|decision|we will|we'll|approved|approve|moving forward|plan is|ownership|roadmap|ship|prioritize)\b", lowered))
        question_signal = "?" in sentence or bool(re.search(r"\b(can|could|would|should|when|what|who|why|how)\b", lowered))
        feedback_signal = bool(re.search(r"\b(thanks|thank you|great job|nice work|well done|kudos|appreciate|shoutout|excellent|awesome)\b", lowered))
        resource_request_signal = bool(re.search(r"\b(need more|need another|need additional|budget|resourcing|headcount|extra engineer|extra designer|support from|help from|need help|need support|resource request|capacity)\b", lowered))

        if direct_alias:
            mention_type = "direct"
            score = 92.0
            if feedback_signal:
                mention_type = "feedback"
                score = 91.0
            elif action_signal:
                mention_type = "action_assignment"
                score = 97.0
            elif question_signal:
                mention_type = "question"
                score = 90.0
            elif resource_request_signal:
                mention_type = "resource_request"
                score = 88.0

            return mention_type, score, action_signal, question_signal, {
                "matched_alias": direct_alias,
                "matched_keyword": keyword_hit,
                "decision_signal": decision_signal,
            }

        if keyword_hit:
            mention_type = "contextual"
            score = 76.0
            if feedback_signal:
                mention_type = "feedback"
                score = 78.0
            elif action_signal:
                mention_type = "action_assignment"
                score = 88.0
            elif decision_signal:
                mention_type = "decision_impact"
                score = 84.0
            elif resource_request_signal:
                mention_type = "resource_request"
                score = 82.0
            elif question_signal:
                mention_type = "question"
                score = 79.0

            return mention_type, score, action_signal, question_signal, {
                "matched_alias": direct_alias,
                "matched_keyword": keyword_hit,
                "decision_signal": decision_signal,
            }

        return None

    def _detect_mentions_with_heuristics(
        self,
        transcript: str,
        user_profiles: List[Dict[str, Any]],
    ) -> List[MentionDetection]:
        mentions: List[MentionDetection] = []
        sentences = self._split_sentences(transcript)
        normalized_users = [self._normalize_user_profile(profile) for profile in user_profiles]

        for index, sentence in enumerate(sentences):
            context_before = sentences[index - 1] if index > 0 else ""
            context_after = sentences[index + 1] if index + 1 < len(sentences) else ""
            context = " ".join(part for part in [context_before, sentence, context_after] if part).strip()

            for normalized_user in normalized_users:
                match = self._classify_sentence_for_user(sentence, normalized_user)
                if not match:
                    continue

                mention_type, relevance_score, is_action_item, is_question, metadata = match
                mentions.append(
                    MentionDetection(
                        user_name=normalized_user.get("name") or normalized_user.get("username") or normalized_user.get("email") or "Unknown",
                        mention_type=mention_type,
                        text=sentence,
                        context=context,
                        relevance_score=relevance_score,
                        is_action_item=is_action_item,
                        is_question=is_question,
                    )
                )

        deduped: List[MentionDetection] = []
        seen = set()
        for mention in mentions:
            key = (mention.user_name.lower(), mention.mention_type, mention.text.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(mention)

        return deduped

    def _extract_json_from_text(self, text: str) -> Dict:
        """Extract JSON object from model text response."""
        stripped = text.strip()
        try:
            return json.loads(stripped)
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", stripped)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}
        return {}

    async def _generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> Dict:
        """Claude primary, Grok fallback. Returns parsed JSON dict."""
        if self.anthropic_client:
            try:
                if hasattr(self.anthropic_client, "messages"):
                    response = await self.anthropic_client.messages.create(  # type: ignore
                        model=settings.ANTHROPIC_MODEL,
                        max_tokens=2000,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_prompt}],
                    )
                    text_parts: List[str] = []
                    for block in response.content:  # type: ignore[attr-defined]
                        block_text = getattr(block, "text", None)
                        if block_text:
                            text_parts.append(block_text)
                    raw_text = "\n".join(text_parts)
                else:
                    prompt = (
                        f"\n\nHuman: {system_prompt}\n\n"
                        f"Task: Return ONLY valid JSON.\n\n{user_prompt}\n\n"
                        "Assistant:"
                    )
                    response = await self.anthropic_client.completions.create(  # type: ignore
                        model=settings.ANTHROPIC_MODEL,
                        prompt=prompt,
                        max_tokens_to_sample=2000,
                        temperature=temperature,
                    )
                    raw_text = getattr(response, "completion", "")

                parsed = self._extract_json_from_text(raw_text)
                if parsed:
                    return parsed
            except Exception as exc:
                logger.warning(f"Claude generation failed, trying Grok fallback: {exc}")

        if self.grok_client:
            try:
                response = await self.grok_client.chat.completions.create(  # type: ignore
                    model=settings.GROK_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                )
                return json.loads(response.choices[0].message.content or "{}")  # type: ignore
            except Exception as exc:
                logger.warning(f"Grok generation failed: {exc}")

        if self.groq_client:
            try:
                response = await self.groq_client.chat.completions.create(  # type: ignore
                    model=settings.GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                )
                return json.loads(response.choices[0].message.content or "{}")  # type: ignore
            except Exception as exc:
                logger.warning(f"Groq generation failed: {exc}")

        return {}
    
    async def detect_mentions(
        self,
        transcript: str,
        user_profiles: List[Dict[str, str]],
        meeting_context: Optional[Dict] = None,
    ) -> List[MentionDetection]:
        """
        Detect user mentions in transcript
        
        Args:
            transcript: Full meeting transcript
            user_profiles: List of user profiles with names, roles, projects
            meeting_context: Additional context about the meeting
        
        Returns:
            List of detected mentions
        """
        logger.info(f"Detecting mentions for {len(user_profiles)} users")

        heuristic_mentions = self._detect_mentions_with_heuristics(transcript, user_profiles)

        if not self.anthropic_client and not self.grok_client and not self.groq_client:
            logger.warning("No NLP provider configured, returning heuristic mentions")
            return heuristic_mentions
        
        # Prepare user context
        user_context = "\n".join([
            f"- {user['name']} ({user.get('role', 'N/A')}): {user.get('projects', 'N/A')}"
            for user in user_profiles
        ])
        
        prompt = f"""You are an expert at detecting when people are mentioned in meeting transcripts.
Analyze the following meeting transcript and detect ALL mentions of the users listed below.

Users to track:
{user_context}

Meeting Transcript:
{transcript}

For each mention found, identify:
1. User name mentioned
2. Type of mention:
   - direct: User explicitly named ("Sarah, can you...")
   - contextual: Discussion about user's work without direct name
   - action_assignment: Task being assigned to user
   - question: Question directed at user
    - feedback: Feedback or praise for user
    - decision_impact: Decision that affects the user's project/team/area even without direct naming
    - resource_request: Request for budget, staffing, support, or capacity from the user or their team
3. The specific text where they're mentioned
4. Surrounding context (2-3 sentences)
5. Relevance score (0-100): How important is this mention to the user?
6. Is this an action item for the user?
7. Is this a question that needs user's response?

Return a JSON object with a top-level 'mentions' array.
"""
        
        mentions_data = await self._generate_json(
            system_prompt="You are an expert meeting analyst.",
            user_prompt=prompt,
            temperature=0.3,
        )
        
        mentions = []
        for mention_dict in mentions_data.get("mentions", []):
            try:
                mentions.append(MentionDetection(**mention_dict))
            except Exception as e:
                logger.warning(f"Failed to parse mention: {e}")

        if heuristic_mentions:
            existing = {
                (mention.user_name.lower(), mention.mention_type, mention.text.strip().lower())
                for mention in mentions
            }
            for mention in heuristic_mentions:
                key = (mention.user_name.lower(), mention.mention_type, mention.text.strip().lower())
                if key not in existing:
                    mentions.append(mention)
                    existing.add(key)
        
        logger.info(f"Detected {len(mentions)} mentions")
        return mentions
    
    async def extract_action_items(
        self,
        transcript: str,
        meeting_attendees: List[str],
    ) -> List[ActionItem]:
        """Extract action items from transcript"""
        logger.info("Extracting action items")

        if not self.anthropic_client and not self.grok_client and not self.groq_client:
            logger.warning("No NLP provider configured, returning fallback action items")
            fallback_items: List[ActionItem] = []
            lines = [line.strip() for line in transcript.splitlines() if line.strip()]
            for line in lines[:3]:
                lowered = line.lower()
                if any(token in lowered for token in ["todo", "action", "follow up", "will ", "need to"]):
                    fallback_items.append(
                        ActionItem(
                            title=line[:80],
                            description=line,
                            owner=None,
                            due_date=None,
                            priority="medium",
                            confidence=0.4,
                        )
                    )
            return fallback_items
        
        prompt = f"""Analyze this meeting transcript and extract ALL action items.

Meeting Attendees:
{', '.join(meeting_attendees)}

Transcript:
{transcript}

For each action item, identify:
1. Clear, concise title
2. Detailed description
3. Owner (person responsible) - must be from attendees list
4. Due date (if mentioned, in YYYY-MM-DD format)
5. Priority (low, medium, high, urgent)
6. Confidence score (0-1): How certain are you this is an actionable task?

Look for:
- Explicit commitments ("I'll do X by Y")
- Task assignments ("Sarah, can you handle Z")
- Follow-up items ("Let's check on this next week")
- Research tasks ("Someone should look into...")

Return a JSON array of action items.
"""
        
        data = await self._generate_json(
            system_prompt="You are an expert at extracting action items from meetings.",
            user_prompt=prompt,
            temperature=0.2,
        )
        
        action_items = []
        for item_dict in data.get("action_items", []):
            try:
                action_items.append(ActionItem(**item_dict))
            except Exception as e:
                logger.warning(f"Failed to parse action item: {e}")
        
        logger.info(f"Extracted {len(action_items)} action items")
        return action_items
    
    async def generate_summary(
        self,
        transcript: str,
        meeting_title: str,
        attendees: List[str],
    ) -> MeetingSummary:
        """Generate comprehensive meeting summary"""
        logger.info("Generating meeting summary")

        if not self.anthropic_client and not self.grok_client and not self.groq_client:
            logger.warning("No NLP provider configured, returning fallback summary")
            cleaned_lines = [line.strip() for line in transcript.splitlines() if line.strip()]
            key_points = cleaned_lines[:5] if cleaned_lines else ["Meeting was uploaded and processed in fallback mode."]
            fallback_actions = await self.extract_action_items(transcript, attendees)
            return MeetingSummary(
                executive_summary=(
                    f"Meeting '{meeting_title}' processed in local fallback mode. "
                    "AI summary is limited until ANTHROPIC_API_KEY, GROK_API_KEY, or GROQ_API_KEY is configured."
                ),
                key_points=key_points,
                decisions=[],
                action_items=fallback_actions,
                discussion_topics=[meeting_title],
                sentiment="neutral",
                sentiment_score=0.0,
            )
        
        prompt = f"""Generate a comprehensive summary of this meeting.

Meeting: {meeting_title}
Attendees: {', '.join(attendees)}

Transcript:
{transcript}

Provide:
1. Executive Summary (2-3 sentences): What was decided, what's next
2. Key Points: 5-7 main takeaways
3. Decisions Made: What was decided, why, alternatives considered, who decided, is it reversible, impact level
4. Action Items: Tasks, owners, deadlines, priorities
5. Discussion Topics: Main themes discussed
6. Overall Sentiment: positive, negative, or neutral
7. Sentiment Score: -1 (very negative) to +1 (very positive)

Be concise but comprehensive. Focus on actionable information.

Return a JSON object.
"""
        
        summary_data = await self._generate_json(
            system_prompt="You are an expert meeting analyst.",
            user_prompt=prompt,
            temperature=0.3,
        )

        if not summary_data:
            cleaned_lines = [line.strip() for line in transcript.splitlines() if line.strip()]
            key_points = cleaned_lines[:5] if cleaned_lines else ["No structured summary returned by model."]
            fallback_actions = await self.extract_action_items(transcript, attendees)
            return MeetingSummary(
                executive_summary=(
                    f"Meeting '{meeting_title}' was processed, but structured JSON was not returned by the model."
                ),
                key_points=key_points,
                decisions=[],
                action_items=fallback_actions,
                discussion_topics=[meeting_title],
                sentiment="neutral",
                sentiment_score=0.0,
            )
        
        # Parse into structured format
        summary = MeetingSummary(
            executive_summary=summary_data.get("executive_summary", ""),
            key_points=summary_data.get("key_points", []),
            decisions=[Decision(**d) for d in summary_data.get("decisions", [])],
            action_items=[ActionItem(**a) for a in summary_data.get("action_items", [])],
            discussion_topics=summary_data.get("discussion_topics", []),
            sentiment=summary_data.get("sentiment", "neutral"),
            sentiment_score=summary_data.get("sentiment_score", 0.0),
        )
        
        logger.info("Summary generated successfully")
        return summary
    
    async def analyze_sentiment(
        self,
        text: str,
    ) -> Dict[str, float]:
        """Analyze sentiment of text"""
        if not self.anthropic_client and not self.grok_client and not self.groq_client:
            return {"sentiment": "neutral", "score": 0.0, "confidence": 0.0}

        prompt = f"""Analyze the sentiment of this text.

Text:
{text}

Return:
- sentiment: positive, negative, or neutral
- score: -1 (very negative) to +1 (very positive)
- confidence: 0 to 1

Return as JSON.
"""
        
        return await self._generate_json(
            system_prompt="You are a sentiment analysis expert.",
            user_prompt=prompt,
            temperature=0.1,
        )
    
    async def generate_embeddings(
        self,
        texts: List[str],
    ) -> List[List[float]]:
        """Generate embeddings for semantic search"""
        logger.warning("Embeddings are disabled in Grok-only mode")
        return [[] for _ in texts]


# Global instance
nlp_service = NLPService()

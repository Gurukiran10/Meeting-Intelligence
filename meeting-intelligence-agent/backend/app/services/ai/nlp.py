"""
AI Services - NLP Analysis Service
"""
import logging
from typing import List, Dict, Optional, Any, Tuple
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
# Gemini uses OpenAI-compatible endpoint (free tier)
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class MentionDetection(BaseModel):
    """Detected mention"""
    user_name: str
    mention_type: str  # direct, contextual, action_assignment, question, feedback, decision_impact, resource_request
    text: str
    context: str
    relevance_score: float
    confidence: float = 0.0
    is_action_item: bool = False
    is_question: bool = False
    sentiment: Optional[str] = None
    sentiment_score: Optional[float] = None
    detection_method: Optional[str] = None
    matched_alias: Optional[str] = None
    matched_keyword: Optional[str] = None
    decision_signal: Optional[bool] = None


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
    mentions: List[MentionDetection] = []
    sentiment: str  # positive, negative, neutral
    sentiment_score: float


class NLPService:
    """Natural Language Processing Service"""
    
    def __init__(self):
        # Gemini — free tier, OpenAI-compatible. Highest priority when key is set.
        self.gemini_client = (
            AsyncOpenAI(
                api_key=settings.GEMINI_API_KEY,
                base_url=getattr(settings, "GEMINI_BASE_URL", GEMINI_BASE_URL),
            )
            if (getattr(settings, "GEMINI_API_KEY", "") and AsyncOpenAI is not None)
            else None
        )  # type: ignore

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

        # Log which providers are active at startup
        active = []
        if self.gemini_client: active.append("Gemini")
        if self.anthropic_client: active.append("Anthropic")
        if self.groq_client: active.append("Groq")
        if self.grok_client: active.append("Grok")
        logger.info("[NLP] Active LLM providers: %s", active or ["offline-heuristic"])

    def _safe_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in re.split(r",|\n|;|\|", value) if part.strip()]
        return []

    def _normalize_decision(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize decision data from different AI provider formats"""
        return {
            "decision": (
                decision_data.get("decision") or 
                decision_data.get("Decision") or 
                decision_data.get("summary") or 
                ""
            ),
            "reasoning": (
                decision_data.get("reasoning") or 
                decision_data.get("reason") or 
                decision_data.get("Why") or 
                ""
            ),
            "alternatives": self._safe_list(
                decision_data.get("alternatives") or 
                decision_data.get("Alternatives Considered") or 
                []
            ),
            "decision_maker": (
                decision_data.get("decision_maker") or 
                decision_data.get("decided_by") or 
                decision_data.get("Who Decided") or 
                None
            ),
            "is_reversible": (
                decision_data.get("is_reversible") or 
                (decision_data.get("Is Reversible") == "Yes" if isinstance(decision_data.get("Is Reversible"), str) else 
                 decision_data.get("reversible") == "Yes" if isinstance(decision_data.get("reversible"), str) else False)
            ),
            "impact_level": (
                decision_data.get("impact_level") or 
                decision_data.get("Impact Level") or 
                "medium"
            ),
        }

    def _normalize_action_item(self, item_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize action item data from different AI provider formats"""
        return {
            "title": (
                item_data.get("title") or 
                item_data.get("task") or 
                item_data.get("Task") or 
                ""
            ),
            "description": (
                item_data.get("description") or 
                item_data.get("task") or 
                item_data.get("Task") or 
                ""
            ),
            "owner": (
                item_data.get("owner") or 
                item_data.get("assignee") or 
                item_data.get("Owner") or 
                item_data.get("assigned_to") or 
                None
            ),
            "due_date": (
                item_data.get("due_date") or 
                item_data.get("deadline") or 
                item_data.get("Deadline") or 
                None
            ),
            "priority": (
                item_data.get("priority") or 
                item_data.get("Priority") or 
                "medium"
            ).lower(),
            "confidence": float(item_data.get("confidence") or item_data.get("Confidence") or 0.8),
        }

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
                        confidence=max(min(relevance_score / 100.0, 1.0), 0.0),
                        is_action_item=is_action_item,
                        is_question=is_question,
                        detection_method="personalized_heuristic",
                        matched_alias=metadata.get("matched_alias"),
                        matched_keyword=metadata.get("matched_keyword"),
                        decision_signal=metadata.get("decision_signal"),
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
        """Extract JSON object from model text response.
        
        Handles:
        - Plain JSON
        - JSON wrapped in ```json ... ``` fences
        - JSON wrapped in ``` ... ``` fences  
        - JSON preceded by explanation text
        """
        if not text:
            return {}

        stripped = text.strip()

        # Step 1: Strip markdown code fences (most common LLM wrapping)
        # e.g. ```json\n{...}\n``` or ```\n{...}\n```
        fence_match = re.search(
            r"```(?:json)?\s*\n?([\s\S]*?)\n?```",
            stripped,
            re.IGNORECASE,
        )
        if fence_match:
            candidate = fence_match.group(1).strip()
            try:
                return json.loads(candidate)
            except Exception:
                pass  # fall through to other strategies

        # Step 2: Try direct parse (already clean JSON)
        try:
            return json.loads(stripped)
        except Exception:
            pass

        # Step 3: Grab the outermost {...} block and parse it
        # This handles cases where the model adds explanation before/after JSON
        match = re.search(r"\{[\s\S]*\}", stripped)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass

        logger.warning("Could not extract JSON from model response. First 200 chars: %s", stripped[:200])
        return {}

    async def _generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 4000) -> Dict:
        """Claude primary, Groq secondary, Grok fallback. Returns parsed JSON dict.
        
        If the complex prompt fails to produce parseable JSON, retries once with
        a dead-simple prompt that most LLMs cannot refuse.
        """
        result = await self._try_generate_json(system_prompt, user_prompt, temperature, max_tokens)
        if result:
            return result

        # ── Retry with a minimal prompt ─────────────────────────────────────
        # The complex prompt sometimes causes the model to add explanation text
        # or wrap output in markdown fences which the normal parser misses.
        # This minimal retry uses a near-empty system prompt and a direct
        # instruction that virtually every LLM honours.
        logger.warning("Full prompt returned no JSON. Retrying with minimal prompt...")
        minimal_system = "You output ONLY raw JSON. No explanation. No markdown."
        minimal_prompt = (
            f"Convert this meeting transcript into a JSON object.\n\n"
            f"Transcript: {user_prompt[-3000:]}\n\n"  # last 3000 chars if long
            f"Return exactly this structure:\n"
            f'{{"summary":"","key_decisions":[],"discussion_topics":[],'
            f'"mentions":[],"action_items":[]}}\n\n'
            f"Fill in all fields from the transcript. Return JSON only."
        )
        result = await self._try_generate_json(minimal_system, minimal_prompt, temperature=0.1, max_tokens=max_tokens)
        if result:
            return result

        logger.error("Both full and retry prompts returned no parseable JSON.")
        return {}

    async def _try_generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 4000) -> Dict:
        """Single-pass attempt: try each configured LLM client, return first valid JSON."""

        # ── 0. Gemini (free tier, OpenAI-compatible) ─────────────────────────
        if self.gemini_client:
            try:
                gemini_model = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
                response = await self.gemini_client.chat.completions.create(
                    model=gemini_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                raw = response.choices[0].message.content or "{}"  # type: ignore
                logger.debug("Gemini raw response (first 300): %s", raw[:300])
                parsed = self._extract_json_from_text(raw)
                if parsed:
                    return parsed
            except Exception as exc:
                logger.warning(f"[NLP] Gemini generation failed: {exc}")

        # ── 1. Anthropic (Claude) ────────────────────────────────────────────
        if self.anthropic_client:
            try:
                if hasattr(self.anthropic_client, "messages"):
                    response = await self.anthropic_client.messages.create(  # type: ignore
                        model=settings.ANTHROPIC_MODEL,
                        max_tokens=max_tokens,
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
                        max_tokens_to_sample=max_tokens,
                        temperature=temperature,
                    )
                    raw_text = getattr(response, "completion", "")

                logger.debug("Anthropic raw response (first 300): %s", raw_text[:300])
                parsed = self._extract_json_from_text(raw_text)
                if parsed:
                    return parsed
            except Exception as exc:
                logger.warning(f"[NLP] Claude generation failed: {exc}")

        # ── 2. Groq (fast inference, free tier) ──────────────────────────────
        if self.groq_client:
            # First attempt: with json_object mode (fastest, most structured)
            try:
                response = await self.groq_client.chat.completions.create(  # type: ignore
                    model=settings.GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                raw = response.choices[0].message.content or "{}"  # type: ignore
                logger.debug("[NLP] Groq raw response (first 300): %s", raw[:300])
                return json.loads(raw)
            except Exception as exc:
                logger.warning(f"[NLP] Groq (json_object mode) failed: {exc} — retrying without response_format")

            # Second attempt: without json_object mode (more compatible)
            try:
                response = await self.groq_client.chat.completions.create(  # type: ignore
                    model=settings.GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt + "\n\nIMPORTANT: Reply with ONLY valid JSON. No markdown, no explanation."},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                raw = response.choices[0].message.content or "{}"  # type: ignore
                logger.debug("[NLP] Groq (plain) raw response (first 300): %s", raw[:300])
                parsed = self._extract_json_from_text(raw)
                if parsed:
                    return parsed
            except Exception as exc:
                logger.warning(f"[NLP] Groq (plain mode) also failed: {exc}")

        # ── 3. Grok (xAI) ────────────────────────────────────────────────────
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
                    max_tokens=max_tokens,
                )
                raw = response.choices[0].message.content or "{}"  # type: ignore
                logger.debug("[NLP] Grok raw response (first 300): %s", raw[:300])
                return json.loads(raw)
            except Exception as exc:
                logger.warning(f"[NLP] Grok generation failed: {exc}")

        logger.error(
            "[NLP] ALL LLM providers failed (Gemini=%s Anthropic=%s Groq=%s Grok=%s). "
            "Falling back to offline heuristic.",
            self.gemini_client is not None,
            self.anthropic_client is not None,
            self.groq_client is not None,
            self.grok_client is not None,
        )
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
        
        # ── FALLBACK ────────────────────────────────────────────────────────
        if not action_items:
            logger.warning("No action items extracted by AI — using offline fallback")
            offline = self._build_offline_summary(transcript, "Meeting")
            return offline.action_items

        logger.info(f"Extracted {len(action_items)} action items")
        return action_items

    async def generate_pre_meeting_guidance(
        self,
        meeting_context: Dict[str, Any],
        user_context: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """Generate lightweight preparation guidance for a user's upcoming meeting."""
        pending_tasks = user_context.get("pending_tasks") or []
        relevant_mentions = user_context.get("relevant_mentions") or []

        fallback_questions: List[str] = []
        for task in pending_tasks[:3]:
            title = str(task.get("title") or "").strip()
            if title:
                fallback_questions.append(f"What is the latest status on '{title}'?")

        fallback_points: List[str] = []
        if pending_tasks:
            fallback_points.append("Be ready to give a short status update on your open tasks.")
        if relevant_mentions:
            fallback_points.append("Address recent mentions or questions that may come up again.")
        if not fallback_points:
            fallback_points.append("Review the agenda and be ready to contribute where your work intersects.")

        if not self.anthropic_client and not self.grok_client and not self.groq_client:
            return {
                "expected_questions": fallback_questions[:5],
                "suggested_points": fallback_points[:5],
            }

        meeting_title = str(meeting_context.get("title") or "").strip()
        agenda = meeting_context.get("agenda") or ""
        attendees = meeting_context.get("attendees") or []

        prompt = f"""Generate a short pre-meeting preparation brief for one attendee.

Meeting Title: {meeting_title}
Agenda: {agenda}
Attendees: {", ".join([str(attendee) for attendee in attendees[:10]])}

User context:
- Pending tasks: {json.dumps(pending_tasks[:5])}
- Relevant mentions: {json.dumps(relevant_mentions[:5])}
- Recent developments: {json.dumps((user_context.get("recent_developments") or [])[:5])}

Return ONLY valid JSON in this exact shape:
{{
  "expected_questions": ["..."],
  "suggested_points": ["..."]
}}

Rules:
- Keep it concise and practical.
- Focus on likely questions this user should be ready to answer.
- Suggested points should help the user contribute clearly in the meeting.
- Maximum 5 items per list.
"""

        data = await self._generate_json(
            system_prompt="You help users prepare for meetings with concise, structured guidance.",
            user_prompt=prompt,
            temperature=0.2,
        )

        expected_questions = self._safe_list(data.get("expected_questions"))[:5] or fallback_questions[:5]
        suggested_points = self._safe_list(data.get("suggested_points"))[:5] or fallback_points[:5]
        return {
            "expected_questions": expected_questions,
            "suggested_points": suggested_points,
        }
    
    def _chunk_transcript(self, transcript: str, max_chars: int = 8000) -> List[str]:
        """Split large transcripts into manageable chunks at sentence boundaries."""
        if len(transcript) <= max_chars:
            return [transcript]

        chunks: List[str] = []
        sentences = self._split_sentences(transcript)
        current_chunk: List[str] = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence) + 1  # +1 for space/newline
            if current_len + sentence_len > max_chars and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(sentence)
            current_len += sentence_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks if chunks else [transcript]


    def _build_offline_summary(self, transcript: str, meeting_title: str) -> "MeetingSummary":
        """Build a real meeting summary purely from the transcript using regex/heuristics.

        Used when ALL LLM providers are unavailable (rate-limited, no credits, etc.).
        Produces a complete MeetingSummary with genuine content — never an error string.
        """
        sentences = self._split_sentences(transcript)
        cleaned = [s.strip() for s in sentences if len(s.strip()) > 5]

        # ── Regex patterns ─────────────────────────────────────────────────
        action_patterns = re.compile(
            r"\b(will|need to|needs to|should|must|going to|has to|have to|please|"
            r"can you|could you|assigned to|responsible for|complete|prepare|review|"
            r"submit|send|finish|update|create|fix|deliver|handle)\b",
            re.IGNORECASE,
        )
        decision_patterns = re.compile(
            r"\b(decided|decision|agreed|approved|moving|moved|launching|we will|"
            r"we\'ll|confirmed|going ahead|plan is|roadmap|prioritize|ship|cancel)\b",
            re.IGNORECASE,
        )
        name_pattern = re.compile(r"^([A-Z][a-z]{1,20})\b[\s,]")
        SKIP_WORDS = {
            "The","This","That","We","Our","All","No","An","A","In","On","At","By",
            "To","For","And","But","Or","So","As","It","If","Is","Are","Was","Were",
            "Has","Have","Had","Let","Please","Also","Then","Now","Meeting","Today",
            "Tomorrow","Next","Last","Thanks","Alright","Great","One","Hi","Hello",
            "Okay","Yes","Yeah","Sure","Right","Well","Good","Awesome","Excellent",
            "Of","My","Your","Their","What","Why","When","Where","How",
            "Can","Could","Should","Would","Will","Do","Does","Did","Let's"
        }
        topic_patterns = re.compile(
            r"\b(dashboard|backend|frontend|api|database|launch|deploy|review|"
            r"documentation|design|testing|bug|feature|sprint|release|integration|"
            r"authentication|security|performance|report|presentation|deadline)\b",
            re.IGNORECASE,
        )

        action_sentences: List[str] = []
        decision_sentences: List[str] = []
        mentions: List["MentionDetection"] = []
        action_items_out: List["ActionItem"] = []
        decisions_out: List["Decision"] = []
        seen_names: set = set()
        seen_decisions: set = set()

        for sentence in cleaned:
            is_action = bool(action_patterns.search(sentence))
            is_decision = bool(decision_patterns.search(sentence))

            if is_action:
                action_sentences.append(sentence)
            if is_decision:
                decision_sentences.append(sentence)

            name_match = name_pattern.match(sentence.strip())
            if name_match:
                name = name_match.group(1)
                if name not in SKIP_WORDS and name.lower() not in seen_names:
                    seen_names.add(name.lower())
                    mentions.append(MentionDetection(
                        user_name=sentence,   # full sentence → passes word-count filter in processor
                        mention_type="action_assignment" if is_action else "direct",
                        text=sentence,
                        context=sentence,
                        relevance_score=88.0,
                        confidence=0.85,
                        detection_method="offline_heuristic",
                    ))
                    if is_action:
                        deadline_match = re.search(
                            r"\b(by\s+\w+|tomorrow|today|friday|monday|tuesday|"
                            r"wednesday|thursday|saturday|sunday|next week|eod|"
                            r"\d{1,2}[\/\-]\d{1,2})\b",
                            sentence, re.IGNORECASE,
                        )
                        deadline = deadline_match.group(0) if deadline_match else None
                        task_text = re.sub(r"^[A-Z][a-z]+[,\s]+", "", sentence).strip()
                        task_text = re.sub(r"\s+", " ", task_text)[:120]
                        action_items_out.append(ActionItem(
                            title=task_text or sentence[:100],
                            description=sentence,
                            owner=name,
                            due_date=deadline,
                            priority="high" if deadline else "medium",
                            confidence=0.80,
                        ))

            if is_decision:
                decision_text = sentence.strip()
                if decision_text.lower() not in seen_decisions:
                    seen_decisions.add(decision_text.lower())
                    decisions_out.append(Decision(
                        decision=decision_text,
                        reasoning="",
                        alternatives=[],
                        decision_maker=None,
                        is_reversible=True,
                        impact_level="medium",
                    ))

        # Build a readable English paragraph instead of raw sentences
        if action_items_out:
            parts = []
            for action in action_items_out[:3]:
                title = (action.title or "").strip().rstrip(".")
                if title.lower().startswith(("is ", "needs ", "will ", "should ", "must ", "can ", "has ")):
                    parts.append(f"{action.owner} {title}")
                else:
                    parts.append(f"{action.owner} is tasked to {title}")
            executive_summary = ", and ".join(parts) + "."
            if decision_sentences:
                executive_summary += " " + decision_sentences[0]
        elif decision_sentences:
            executive_summary = " ".join(decision_sentences[:3])
        elif cleaned:
            executive_summary = " ".join(cleaned[:3])
        else:
            executive_summary = "The meeting covered team tasks and responsibilities."

        topic_words: List[str] = []
        for sentence in cleaned:
            for match in topic_patterns.finditer(sentence):
                word = match.group(0).capitalize()
                if word not in topic_words:
                    topic_words.append(word)
            if len(topic_words) >= 5:
                break
        if not topic_words:
            topic_words = [meeting_title] if meeting_title else ["General discussion"]

        logger.info(
            f"Offline extraction: {len(mentions)} mentions, "
            f"{len(action_items_out)} action items, {len(decisions_out)} decisions"
        )
        return MeetingSummary(
            executive_summary=executive_summary,
            key_points=[s for s in action_sentences[:5]],
            decisions=decisions_out,
            action_items=action_items_out,
            discussion_topics=topic_words,
            mentions=mentions,
            sentiment="neutral",
            sentiment_score=0.0,
        )

    async def generate_summary(
        self,
        transcript: str,
        meeting_title: str,
        attendees: List[str],
    ) -> MeetingSummary:
        """Generate comprehensive meeting summary with structured extraction"""
        logger.info("Generating meeting summary")

        if not self.anthropic_client and not self.grok_client and not self.groq_client:
            logger.warning("No NLP provider configured — using offline heuristic extraction")
            return self._build_offline_summary(transcript, meeting_title)

        # Chunk large transcripts to avoid token limits
        chunks = self._chunk_transcript(transcript, max_chars=8000)
        if len(chunks) > 1:
            logger.info(f"Transcript split into {len(chunks)} chunks for processing")

        system_prompt = (
            "You are a STRICT JSON generator for a meeting intelligence system.\n\n"
            "\u26a0\ufe0f CRITICAL:\n"
            "* Output MUST be valid JSON\n"
            "* NO explanation\n"
            "* NO markdown\n"
            "* NO extra text\n"
            "* If you fail, the system will reject your response"
        )

        prompt = f"""You are a STRICT JSON generator for a meeting intelligence system.

\u26a0\ufe0f CRITICAL:
* Output MUST be valid JSON
* NO explanation
* NO markdown
* NO extra text
* If you fail, the system will reject your response

---

INPUT TRANSCRIPT:
{transcript}

---

OUTPUT FORMAT:
{{
  "summary": "",
  "key_decisions": [],
  "discussion_topics": [],
  "mentions": [],
  "action_items": []
}}

---

### TASKS

### 1. SUMMARY
* 2-3 sentences
* Include: what was discussed, key actions, any decisions

---

### 2. MENTIONS (VERY IMPORTANT)
Extract ALL people mentioned in the transcript.

STRICT RULES:
* DO NOT miss any name
* If 3 people are mentioned \u2192 return 3 mentions
* Each mention MUST include FULL sentence

FORMAT:
{{"name": "Person Name", "sentence": "FULL sentence from transcript"}}

EXAMPLE:
Input: "Sara, complete dashboard. Guru, review APIs. John, prepare docs."
Output:
[
  {{"name": "Sara", "sentence": "Sara, complete dashboard."}},
  {{"name": "Guru", "sentence": "Guru, review APIs."}},
  {{"name": "John", "sentence": "John, prepare docs."}}
]

\u274c WRONG: {{"name": "Sara", "sentence": "Sara"}}

---

### 3. ACTION ITEMS
Extract ALL tasks.

FORMAT:
{{"task": "what needs to be done", "owner": "Person Name", "deadline": "date or null"}}

RULES:
* EVERY owner MUST exist in mentions
* DO NOT skip tasks

---

### 4. KEY DECISIONS
Extract decisions.
FORMAT: {{"decision": "text"}}
Example: "Launch moved to next week"

---

### 5. DISCUSSION TOPICS
Extract 3-5 meaningful topics.
Examples: "Dashboard development", "Backend APIs", "Documentation", "Launch planning"

---

### FINAL VALIDATION (MANDATORY BEFORE OUTPUT)
* Count names in transcript \u2192 count mentions \u2192 MUST MATCH
* If Sara, Guru, John exist \u2192 ALL must be in mentions
* Each mention must have full sentence
* Action items must not be empty if tasks exist
* JSON must be valid

---

RETURN JSON ONLY."""

        summary_data = await self._generate_json(
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=0.1,
        )

        # ── FALLBACK ────────────────────────────────────────────────────────
        # If the model returned nothing (rate limit, error, etc), use the offline heuristic
        if not summary_data or not summary_data.get("summary"):
            logger.warning("AI summary generation returned no data — falling back to offline heuristic")
            return self._build_offline_summary(transcript, meeting_title)

        logger.info(f"Summary data returned: {summary_data}")
        
        # Handle nested response from Groq API
        if summary_data and "meeting_summary" in summary_data:
            summary_data = summary_data["meeting_summary"]
            logger.info(f"Extracted nested summary data: {summary_data}")

        if not summary_data:
            logger.warning(
                "All LLM providers unavailable (rate limit / no credits). "
                "Falling back to heuristic offline extraction."
            )
            return self._build_offline_summary(transcript, meeting_title)


        # ── Parse decisions ─────────────────────────────────────────────
        # New prompt returns flat strings: ["decision text", ...]
        # Old prompt returned objects: [{"decision": ..., "reasoning": ...}]
        decisions_data = (
            summary_data.get("key_decisions") or
            summary_data.get("decisions") or
            summary_data.get("decisions_made") or
            summary_data.get("Decisions Made") or
            summary_data.get("Decisions") or
            []
        )
        if isinstance(decisions_data, dict):
            decisions_data = list(decisions_data.values()) if decisions_data else []
        elif not isinstance(decisions_data, list):
            decisions_data = []

        decisions = []
        for d in decisions_data:
            try:
                if isinstance(d, str):
                    # Flat string from new prompt → wrap as Decision object
                    decisions.append(Decision(
                        decision=d,
                        reasoning="",
                        alternatives=[],
                        decision_maker=None,
                        is_reversible=True,
                        impact_level="medium",
                    ))
                elif isinstance(d, dict):
                    decisions.append(Decision(**self._normalize_decision(d)))
            except Exception as e:
                logger.warning(f"Failed to parse decision: {e}")
        logger.info(f"Successfully parsed {len(decisions)} decisions from {len(decisions_data)} data items")

        # ── Parse action items ──────────────────────────────────────────
        # New prompt returns: {task, assignee, deadline, priority}
        # Old prompt returned: {title, description, owner, due_date, priority, confidence}
        # _normalize_action_item handles both via field aliases
        action_items_data = (
            summary_data.get("action_items") or
            summary_data.get("Action Items") or
            []
        )
        if isinstance(action_items_data, dict):
            action_items_data = list(action_items_data.values()) if action_items_data else []
        elif not isinstance(action_items_data, list):
            action_items_data = []

        action_items = []
        for a in action_items_data:
            if isinstance(a, dict):
                try:
                    action_items.append(ActionItem(**self._normalize_action_item(a)))
                except Exception as e:
                    logger.warning(f"Failed to parse action item: {e}")

        # ── Parse mentions ──────────────────────────────────────────────
        # New prompt returns: {name, sentence, type, confidence}
        # Old prompt returned: {name, context}
        mention_items_data = summary_data.get("mentions") or summary_data.get("Mentions") or []
        if isinstance(mention_items_data, dict):
            mention_items_data = list(mention_items_data.values()) if mention_items_data else []
        elif not isinstance(mention_items_data, list):
            mention_items_data = []

        mentions: List[MentionDetection] = []
        seen_mention_names: set = set()
        for mention_item in mention_items_data:
            try:
                if isinstance(mention_item, dict):
                    name = str(
                        mention_item.get("name") or
                        mention_item.get("user_name") or
                        mention_item.get("user") or ""
                    ).strip()
                    if not name:
                        continue

                    # Prefer the full verbatim sentence over bare context
                    sentence = str(
                        mention_item.get("sentence") or
                        mention_item.get("text") or
                        mention_item.get("context") or ""
                    ).strip()

                    # Fall back to constructing a minimal meaningful sentence
                    # so the processor word-count filter never drops it.
                    if not sentence or sentence.lower() == name.lower():
                        sentence = f"{name} was mentioned in this meeting."

                    mention_type = str(
                        mention_item.get("type") or
                        mention_item.get("mention_type") or "direct"
                    ).strip()
                    confidence = float(mention_item.get("confidence") or 0.9)

                    # Deduplicate by name (keep first / most informative)
                    name_key = name.lower()
                    if name_key in seen_mention_names:
                        continue
                    seen_mention_names.add(name_key)

                    mentions.append(MentionDetection(
                        user_name=name,
                        mention_type=mention_type,
                        text=sentence,
                        context=sentence,
                        relevance_score=85.0,
                        confidence=confidence,
                        detection_method="ai_structured_extraction",
                    ))

                elif isinstance(mention_item, str):
                    name = mention_item.strip()
                    if not name:
                        continue
                    name_key = name.lower()
                    if name_key in seen_mention_names:
                        continue
                    seen_mention_names.add(name_key)
                    mentions.append(MentionDetection(
                        user_name=name,
                        mention_type="direct",
                        text=f"{name} was mentioned in this meeting.",
                        context="",
                        relevance_score=75.0,
                        confidence=0.8,
                        detection_method="ai_structured_extraction",
                    ))
            except Exception as e:
                logger.warning(f"Failed to parse mention: {e}")

        # ── Cross-check: ensure every action_item owner has a mention ───
        # This guarantees consistency even when the LLM misses a mention.
        for a in action_items_data:
            if not isinstance(a, dict):
                continue
            owner = str(
                a.get("owner") or a.get("assignee") or ""
            ).strip()
            if not owner:
                continue
            owner_key = owner.lower()
            if owner_key not in seen_mention_names:
                task_text = str(a.get("task") or a.get("title") or "").strip()
                sentence = f"{owner} will {task_text}." if task_text else f"{owner} was mentioned in this meeting."
                mentions.append(MentionDetection(
                    user_name=owner,
                    mention_type="action_assignment",
                    text=sentence,
                    context=sentence,
                    relevance_score=90.0,
                    confidence=0.85,
                    detection_method="action_item_crosscheck",
                ))
                seen_mention_names.add(owner_key)
                logger.info(f"Auto-added missing mention for action owner: '{owner}'")  

        # ── Parse topics ────────────────────────────────────────────────
        # New prompt returns "discussion_topics", old returned "topics"
        discussion_topics = self._safe_list(
            summary_data.get("discussion_topics") or
            summary_data.get("topics") or
            summary_data.get("Discussion Topics") or
            []
        )

        # ── Build MeetingSummary ────────────────────────────────────────
        # New prompt returns "summary", old returned "executive_summary"
        summary = MeetingSummary(
            executive_summary=(
                summary_data.get("summary") or
                summary_data.get("executive_summary") or
                summary_data.get("Executive Summary") or
                ""
            ),
            key_points=self._safe_list(
                summary_data.get("key_points") or
                summary_data.get("Key Points") or
                []
            ),
            decisions=decisions,
            action_items=action_items,
            discussion_topics=discussion_topics,
            mentions=mentions,
            sentiment=(
                summary_data.get("sentiment") or
                summary_data.get("overall_sentiment") or
                summary_data.get("Overall Sentiment") or
                "neutral"
            ).lower(),
            sentiment_score=float(summary_data.get("sentiment_score") or summary_data.get("Sentiment Score") or 0.0),
        )

        logger.info(
            f"Summary parsed successfully: {len(decisions)} decisions, {len(action_items)} action items, {len(mentions)} mentions"
        )
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

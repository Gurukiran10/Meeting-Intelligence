"""
Integrations API Endpoints
Connect/disconnect/test Slack, Linear, Zoom, Google Calendar, Microsoft Teams
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
import httpx
import base64
import os
import re
import json
import uuid
from urllib.parse import urlencode

from app.core.database import get_db
from app.core.database import SessionLocal
from app.core.config import settings
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.models.meeting import Meeting
from app.models.transcript import Transcript
from app.services.mentions import detect_and_store_mentions

router = APIRouter()
AUTO_SYNC_KEY = "_auto_sync"


def _forced_slack_channel() -> Optional[str]:
    forced = (settings.SLACK_TEST_CHANNEL_ID or "").strip()
    if settings.SLACK_FORCE_TEST_CHANNEL_ONLY and forced:
        return forced
    return None


# ─────────────────── Schemas ───────────────────

class SlackConnectRequest(BaseModel):
    bot_token: str
    default_channel: Optional[str] = "#testing"
    webhook_url: Optional[str] = None


class LinearConnectRequest(BaseModel):
    api_key: str


class ZoomConnectRequest(BaseModel):
    account_id: str
    client_id: str
    client_secret: str


class GoogleConnectRequest(BaseModel):
    api_key: Optional[str] = None
    calendar_id: Optional[str] = "primary"
    service_account_json: Optional[str] = None
    oauth_refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


class GoogleOAuthCodeRequest(BaseModel):
    code: str
    redirect_uri: Optional[str] = None
    calendar_id: Optional[str] = "primary"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


class MicrosoftConnectRequest(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str
    calendar_user: Optional[str] = None


class AutoSyncPlatformRequest(BaseModel):
    platform: str
    enabled: bool


class CapturePolicyRequest(BaseModel):
    auto_join_enabled: bool = True
    auto_transcription_enabled: bool = True
    retention_days: int = 30
    require_explicit_consent: bool = True
    respect_no_record_requests: bool = True
    smart_recording_enabled: bool = True
    min_team_size: int = 1
    include_keywords: List[str] = []
    exclude_keywords: List[str] = []
    required_tags: List[str] = []
    rules: List[Dict[str, Any]] = []


class CapturePolicyEvaluateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    attendee_count: int = 0
    tags: List[str] = []
    platform: Optional[str] = None
    scheduled_start: Optional[str] = None


class MeetingConsentRequest(BaseModel):
    recording_consent: bool
    no_record_requested: bool = False
    reason: Optional[str] = None


class MeetingConsentOptOutRequest(BaseModel):
    attendee_name: Optional[str] = None
    attendee_email: Optional[str] = None
    reason: Optional[str] = None


class BotJoinRequest(BaseModel):
    force: bool = False


class LiveTranscriptionStartRequest(BaseModel):
    bot_id: Optional[str] = None


class LiveTranscriptSegmentRequest(BaseModel):
    text: str
    start_time: float
    end_time: float
    speaker_name: Optional[str] = None
    language: str = "en"
    confidence: float = 1.0
    is_final: bool = True


# ─────────────────── Helpers ───────────────────

from sqlalchemy.orm.attributes import flag_modified

def _get_integration(user: User, key: str) -> Dict[str, Any]:
    integrations = user.integrations or {}
    return integrations.get(key, {})


def _save_integration(db: Session, user: User, key: str, data: Dict[str, Any]):
    import copy
    integrations = copy.deepcopy(user.integrations or {})
    integrations[key] = data
    user.integrations = integrations
    flag_modified(user, "integrations")
    db.commit()
    db.refresh(user)


def _set_auto_sync_status(
    db: Session,
    user: User,
    platform: str,
    status: str,
    detail: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
):
    integrations = dict(user.integrations or {})
    auto_sync = dict(integrations.get(AUTO_SYNC_KEY) or {})
    by_platform = dict(auto_sync.get("platforms") or {})
    existing_entry = dict(by_platform.get(platform) or {})

    entry: Dict[str, Any] = {
        "status": status,
        "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    if detail:
        entry["detail"] = detail
    if metrics:
        entry["metrics"] = metrics
    if status == "ok":
        entry["last_success_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        entry["last_error_at"] = existing_entry.get("last_error_at")
    else:
        entry["last_error_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        entry["last_success_at"] = existing_entry.get("last_success_at")

    by_platform[platform] = entry
    auto_sync["platforms"] = by_platform
    auto_sync["last_run_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    integrations[AUTO_SYNC_KEY] = auto_sync
    user.integrations = integrations
    db.commit()


def _default_auto_sync_enabled(user: User, platform: str) -> bool:
    integration = _get_integration(user, platform)
    if platform == "zoom":
        return bool(integration.get("account_id"))
    if platform == "google":
        return bool(
            integration.get("api_key")
            or integration.get("service_account_json")
            or integration.get("refresh_token")
            or integration.get("oauth_refresh_token")
            or integration.get("access_token")
        )
    if platform == "microsoft":
        return bool(integration.get("tenant_id"))
    return False


def _get_auto_sync_state(user: User) -> Dict[str, Any]:
    integrations = user.integrations or {}
    auto_sync = dict(integrations.get(AUTO_SYNC_KEY) or {})
    enabled = dict(auto_sync.get("enabled") or {})
    platforms = dict(auto_sync.get("platforms") or {})

    for platform in ("zoom", "google", "microsoft"):
        if platform not in enabled:
            enabled[platform] = _default_auto_sync_enabled(user, platform)

    last_run_at = auto_sync.get("last_run_at")
    next_run_at = None
    if last_run_at:
        parsed = _parse_iso_datetime(last_run_at)
        if parsed:
            next_run_at = (
                parsed + timedelta(minutes=max(settings.INTEGRATION_AUTO_SYNC_INTERVAL_MINUTES, 5))
            ).replace(microsecond=0).isoformat() + "Z"

    return {
        "enabled": enabled,
        "platforms": platforms,
        "last_run_at": last_run_at,
        "next_run_at": next_run_at,
    }


def _is_auto_sync_enabled(user: User, platform: str) -> bool:
    state = _get_auto_sync_state(user)
    return bool((state.get("enabled") or {}).get(platform, False))


def _set_auto_sync_enabled(db: Session, user: User, platform: str, enabled: bool) -> Dict[str, Any]:
    integrations = dict(user.integrations or {})
    auto_sync = dict(integrations.get(AUTO_SYNC_KEY) or {})
    enabled_map = dict(auto_sync.get("enabled") or {})
    enabled_map[platform] = enabled
    auto_sync["enabled"] = enabled_map
    integrations[AUTO_SYNC_KEY] = auto_sync
    user.integrations = integrations
    db.commit()
    db.refresh(user)
    return _get_auto_sync_state(user)


def _remove_integration(db: Session, user: User, key: str):
    integrations = dict(user.integrations or {})
    integrations.pop(key, None)
    user.integrations = integrations
    db.commit()


def _default_capture_policy() -> Dict[str, Any]:
    return {
        "auto_join_enabled": True,
        "auto_transcription_enabled": True,
        "retention_days": 30,
        "require_explicit_consent": True,
        "respect_no_record_requests": True,
        "smart_recording_enabled": True,
        "min_team_size": 1,
        "include_keywords": [],
        "exclude_keywords": [],
        "required_tags": [],
        "rules": [],
        "updated_at": None,
    }


def _normalize_retention_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = 30

    if days in {7, 30, 90}:
        return days
    if days <= 18:
        return 7
    if days <= 59:
        return 30
    return 90


def _normalize_smart_rules(raw_rules: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []

    for index, raw_rule in enumerate(raw_rules or []):
        if not isinstance(raw_rule, dict):
            continue

        action = str(raw_rule.get("action", "record")).strip().lower()
        action = "record" if action == "record" else "skip"

        rule_id = str(raw_rule.get("id") or f"rule_{index + 1}").strip() or f"rule_{index + 1}"
        name = str(raw_rule.get("name") or rule_id).strip() or rule_id

        try:
            priority = int(raw_rule.get("priority", index + 1))
        except (TypeError, ValueError):
            priority = index + 1

        def _to_list(key: str) -> List[str]:
            return [
                str(item).strip().lower()
                for item in (raw_rule.get(key) or [])
                if str(item).strip()
            ]

        min_attendees = raw_rule.get("min_attendees")
        max_attendees = raw_rule.get("max_attendees")
        start_hour = raw_rule.get("start_hour")
        end_hour = raw_rule.get("end_hour")

        try:
            min_attendees = int(min_attendees) if min_attendees is not None else None
        except (TypeError, ValueError):
            min_attendees = None

        try:
            max_attendees = int(max_attendees) if max_attendees is not None else None
        except (TypeError, ValueError):
            max_attendees = None

        try:
            start_hour = int(start_hour) if start_hour is not None else None
        except (TypeError, ValueError):
            start_hour = None

        try:
            end_hour = int(end_hour) if end_hour is not None else None
        except (TypeError, ValueError):
            end_hour = None

        if start_hour is not None:
            start_hour = max(0, min(start_hour, 23))
        if end_hour is not None:
            end_hour = max(1, min(end_hour, 24))

        normalized.append(
            {
                "id": rule_id,
                "name": name,
                "enabled": bool(raw_rule.get("enabled", True)),
                "priority": max(1, min(priority, 999)),
                "action": action,
                "keywords_any": _to_list("keywords_any"),
                "keywords_all": _to_list("keywords_all"),
                "tags_any": _to_list("tags_any"),
                "platforms": _to_list("platforms"),
                "min_attendees": min_attendees,
                "max_attendees": max_attendees,
                "start_hour": start_hour,
                "end_hour": end_hour,
            }
        )

    normalized.sort(key=lambda rule: (rule.get("priority", 999), rule.get("id", "")))
    return normalized[:50]


def _get_capture_policy(user: User) -> Dict[str, Any]:
    integrations = user.integrations or {}
    stored = integrations.get("_capture_policy") or {}
    merged = _default_capture_policy()
    merged.update(stored)

    merged["retention_days"] = _normalize_retention_days(merged.get("retention_days", 30))
    merged["min_team_size"] = max(1, min(int(merged.get("min_team_size", 1)), 200))
    merged["include_keywords"] = [str(k).strip().lower() for k in (merged.get("include_keywords") or []) if str(k).strip()]
    merged["exclude_keywords"] = [str(k).strip().lower() for k in (merged.get("exclude_keywords") or []) if str(k).strip()]
    merged["required_tags"] = [str(k).strip().lower() for k in (merged.get("required_tags") or []) if str(k).strip()]
    merged["rules"] = _normalize_smart_rules(merged.get("rules"))
    return merged


def _save_capture_policy(db: Session, user: User, policy: Dict[str, Any]) -> Dict[str, Any]:
    integrations = dict(user.integrations or {})
    merged = _default_capture_policy()
    merged.update(policy)
    merged["retention_days"] = _normalize_retention_days(merged.get("retention_days", 30))
    merged["min_team_size"] = max(1, min(int(merged.get("min_team_size", 1)), 200))
    merged["include_keywords"] = [str(k).strip().lower() for k in (merged.get("include_keywords") or []) if str(k).strip()]
    merged["exclude_keywords"] = [str(k).strip().lower() for k in (merged.get("exclude_keywords") or []) if str(k).strip()]
    merged["required_tags"] = [str(k).strip().lower() for k in (merged.get("required_tags") or []) if str(k).strip()]
    merged["rules"] = _normalize_smart_rules(merged.get("rules"))
    merged["updated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    integrations["_capture_policy"] = merged
    user.integrations = integrations
    db.commit()
    db.refresh(user)
    return merged


def _rule_matches_meeting(
    rule: Dict[str, Any],
    haystack: str,
    tags: List[str],
    attendee_count: int,
    platform: Optional[str],
    scheduled_start: Optional[datetime],
) -> bool:
    if not rule.get("enabled", True):
        return False

    keywords_any = rule.get("keywords_any") or []
    if keywords_any and not any(keyword in haystack for keyword in keywords_any):
        return False

    keywords_all = rule.get("keywords_all") or []
    if keywords_all and not all(keyword in haystack for keyword in keywords_all):
        return False

    tags_any = rule.get("tags_any") or []
    if tags_any and not any(tag in tags for tag in tags_any):
        return False

    platforms = rule.get("platforms") or []
    if platforms and platform and platform not in platforms:
        return False
    if platforms and not platform:
        return False

    min_attendees = rule.get("min_attendees")
    if min_attendees is not None and attendee_count < int(min_attendees):
        return False

    max_attendees = rule.get("max_attendees")
    if max_attendees is not None and attendee_count > int(max_attendees):
        return False

    start_hour = rule.get("start_hour")
    end_hour = rule.get("end_hour")
    if (start_hour is not None or end_hour is not None) and scheduled_start is not None:
        hour = scheduled_start.hour
        normalized_start = int(start_hour) if start_hour is not None else 0
        normalized_end = int(end_hour) if end_hour is not None else 24
        if not (normalized_start <= hour < normalized_end):
            return False

    return True


def _evaluate_capture_policy(
    policy: Dict[str, Any],
    title: str,
    description: Optional[str],
    attendee_count: int,
    tags: List[str],
    platform: Optional[str] = None,
    scheduled_start: Optional[datetime] = None,
) -> Dict[str, Any]:
    normalized_title = (title or "").strip().lower()
    normalized_description = (description or "").strip().lower()
    normalized_tags = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    haystack = f"{normalized_title} {normalized_description}".strip()

    reasons: List[str] = []
    should_record = True

    if not policy.get("smart_recording_enabled", True):
        reasons.append("smart_recording_disabled")
        return {"should_record": True, "reasons": reasons}

    for rule in policy.get("rules") or []:
        if _rule_matches_meeting(rule, haystack, normalized_tags, attendee_count, platform, scheduled_start):
            should_record = (rule.get("action") or "record") == "record"
            reasons.append(f"matched_rule:{rule.get('id')}")
            return {
                "should_record": should_record,
                "reasons": reasons,
                "matched_rule": {
                    "id": rule.get("id"),
                    "name": rule.get("name"),
                    "action": rule.get("action"),
                },
            }

    min_team_size = int(policy.get("min_team_size", 1))
    if attendee_count < min_team_size:
        should_record = False
        reasons.append(f"team_size_below_min({attendee_count}<{min_team_size})")

    include_keywords = policy.get("include_keywords") or []
    if include_keywords and not any(keyword in haystack for keyword in include_keywords):
        should_record = False
        reasons.append("missing_include_keywords")

    exclude_keywords = policy.get("exclude_keywords") or []
    if exclude_keywords and any(keyword in haystack for keyword in exclude_keywords):
        should_record = False
        reasons.append("contains_excluded_keywords")

    required_tags = policy.get("required_tags") or []
    if required_tags and not any(tag in normalized_tags for tag in required_tags):
        should_record = False
        reasons.append("missing_required_tags")

    if should_record and not reasons:
        reasons.append("matches_smart_recording_rules")

    return {"should_record": should_record, "reasons": reasons}


def _apply_capture_policy_metadata(
    policy: Dict[str, Any],
    title: str,
    description: Optional[str],
    attendee_count: int,
    tags: List[str],
    platform: Optional[str] = None,
    scheduled_start: Optional[datetime] = None,
) -> Dict[str, Any]:
    evaluation = _evaluate_capture_policy(
        policy,
        title,
        description,
        attendee_count,
        tags,
        platform=platform,
        scheduled_start=scheduled_start,
    )
    return {
        "capture_policy_snapshot": {
            "auto_join_enabled": policy.get("auto_join_enabled", True),
            "auto_transcription_enabled": policy.get("auto_transcription_enabled", True),
            "require_explicit_consent": policy.get("require_explicit_consent", True),
            "respect_no_record_requests": policy.get("respect_no_record_requests", True),
            "retention_days": policy.get("retention_days", 30),
            "smart_recording_enabled": policy.get("smart_recording_enabled", True),
            "rules": policy.get("rules", []),
        },
        "smart_recording": evaluation,
    }


def _meeting_capture_block_reason(meeting: Meeting, policy: Dict[str, Any]) -> Optional[str]:
    metadata = dict(meeting.meeting_metadata or {})
    if policy.get("require_explicit_consent", True) and not meeting.recording_consent:
        return "explicit_consent_required"
    if policy.get("respect_no_record_requests", True) and metadata.get("no_record_requested"):
        return "no_record_requested"

    smart = metadata.get("smart_recording") or {}
    if policy.get("smart_recording_enabled", True) and smart.get("should_record") is False:
        return "smart_recording_policy_block"

    return None


def _build_consent_announcement(meeting: Meeting, policy: Dict[str, Any]) -> str:
    retention_days = _normalize_retention_days(policy.get("retention_days", 30))
    base = (
        f"This meeting is being captured for transcription and analysis. "
        f"Recordings are retained for {retention_days} days. "
        "If you do not consent, say 'no record' or opt out in the meeting controls."
    )
    if policy.get("require_explicit_consent", True):
        return base + " Explicit consent is required before recording starts."
    return base


def _set_bot_session_metadata(
    meeting: Meeting,
    status: str,
    detail: Optional[str] = None,
    bot_id: Optional[str] = None,
    announcement_text: Optional[str] = None,
):
    metadata = dict(meeting.meeting_metadata or {})
    bot_session = dict(metadata.get("bot_session") or {})
    bot_session["status"] = status
    bot_session["updated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    if detail:
        bot_session["detail"] = detail
    if bot_id:
        bot_session["bot_id"] = bot_id
    if announcement_text:
        bot_session["announcement_text"] = announcement_text
        bot_session["announcement_delivered"] = True
    metadata["bot_session"] = bot_session
    meeting.meeting_metadata = metadata


def _launch_bot_for_meeting(
    meeting: Meeting,
    policy: Dict[str, Any],
    force: bool = False,
) -> Dict[str, Any]:
    if not meeting.meeting_url:
        _set_bot_session_metadata(meeting, "skipped", "missing_meeting_url")
        return {"status": "skipped", "reason": "missing_meeting_url"}

    if not force:
        blocked = _meeting_capture_block_reason(meeting, policy)
        if blocked:
            _set_bot_session_metadata(meeting, "blocked", blocked)
            return {"status": "blocked", "reason": blocked}

    bot_id = f"bot_{uuid.uuid4().hex[:12]}"
    announcement_text = _build_consent_announcement(meeting, policy)
    _set_bot_session_metadata(meeting, "joined", "bot_joined", bot_id=bot_id, announcement_text=announcement_text)
    if meeting.status == "scheduled":
        meeting.status = "in_progress"
    if policy.get("auto_transcription_enabled", True):
        meeting.transcription_status = "processing"

    return {
        "status": "joined",
        "bot_id": bot_id,
        "platform": meeting.platform,
        "meeting_id": str(meeting.id),
    }


def _run_recording_retention_cleanup_for_user(db: Session, user: User) -> Dict[str, int]:
    policy = _get_capture_policy(user)
    retention_days = _normalize_retention_days(policy.get("retention_days", 30))
    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    meetings = db.execute(
        select(Meeting).where(
            Meeting.organizer_id == user.id,
            Meeting.recording_path.is_not(None),
            Meeting.created_at < cutoff,
        )
    ).scalars().all()

    files_deleted = 0
    meetings_cleaned = 0

    for meeting in meetings:
        recording_path = (meeting.recording_path or "").strip()
        if recording_path:
            try:
                if os.path.exists(recording_path):
                    os.remove(recording_path)
                    files_deleted += 1
            except OSError:
                pass

        meeting.recording_path = None
        meeting.recording_url = None
        meeting.recording_size_mb = None
        metadata = dict(meeting.meeting_metadata or {})
        metadata["retention_cleanup_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        metadata["retention_days"] = retention_days
        meeting.meeting_metadata = metadata
        meetings_cleaned += 1

    if meetings_cleaned:
        db.commit()

    return {"meetings_cleaned": meetings_cleaned, "files_deleted": files_deleted}


def _mask(value: str, show: int = 6) -> str:
    if not value:
        return ""
    return value[:show] + "..." + value[-4:] if len(value) > show + 4 else "***"


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _parse_google_datetime(date_time_value: Optional[str], date_value: Optional[str]) -> Optional[datetime]:
    if date_time_value:
        return _parse_iso_datetime(date_time_value)

    if date_value:
        try:
            return datetime.fromisoformat(date_value)
        except ValueError:
            return None

    return None


def _format_zoom_api_error(resp: httpx.Response, default_prefix: str) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"{default_prefix}: {resp.text}"

    message = data.get("message") or resp.text
    missing_scopes = message if "does not contain scopes" in message else ""

    if "meeting:read:list_meetings" in missing_scopes:
        return (
            "Zoom is connected, but the token is missing the required scope "
            "`meeting:read:list_meetings:admin`. Add that scope in the Zoom Marketplace, "
            "then disconnect and reconnect Zoom in this app."
        )

    if "cloud_recording:read:list_user_recordings" in missing_scopes:
        return (
            "Zoom is connected, but the token is missing the history scope "
            "`cloud_recording:read:list_user_recordings:admin`. Add that scope in the Zoom Marketplace, "
            "then disconnect and reconnect Zoom in this app. Keep "
            "`cloud_recording:read:recording:admin` enabled as well for recording and transcript access."
        )

    if "recording" in missing_scopes or "cloud_recording" in missing_scopes:
        return (
            "Zoom is connected, but the token is missing the recording scope "
            "`cloud_recording:read:recording:admin`. Add that scope in the Zoom Marketplace, "
            "then disconnect and reconnect Zoom in this app."
        )

    return f"{default_prefix}: {message}"


def _parse_zoom_timestamp_to_seconds(value: str) -> Optional[float]:
    match = re.match(r"^(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$", value.strip())
    if not match:
        return None

    hours, minutes, seconds, milliseconds = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if milliseconds:
        total += int(milliseconds.ljust(3, "0")) / 1000
    return float(total)


def _parse_zoom_transcript_segments(transcript_text: str) -> List[Dict[str, Any]]:
    cleaned = transcript_text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    segments: List[Dict[str, Any]] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0].upper() == "WEBVTT":
            continue

        if len(lines) >= 2 and re.match(r"^\d+$", lines[0]):
            lines = lines[1:]

        if not lines or "-->" not in lines[0]:
            continue

        time_line = lines[0]
        text_lines = lines[1:]
        start_raw, end_raw = [part.strip() for part in time_line.split("-->", 1)]
        start_seconds = _parse_zoom_timestamp_to_seconds(start_raw)
        end_seconds = _parse_zoom_timestamp_to_seconds(end_raw.split()[0])
        text = " ".join(text_lines).strip()

        if start_seconds is None or end_seconds is None or not text:
            continue

        segments.append(
            {
                "start_time": start_seconds,
                "end_time": end_seconds,
                "text": text,
            }
        )

    if segments:
        return segments

    fallback_text = re.sub(r"^WEBVTT\s*", "", cleaned, flags=re.IGNORECASE).strip()
    if fallback_text:
        return [
            {
                "start_time": 0.0,
                "end_time": 0.0,
                "text": fallback_text,
            }
        ]

    return []


def _select_zoom_recording_file(recording_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    preferred_types = ["MP4", "M4A"]
    completed_files = [f for f in recording_files if f.get("status", "completed") == "completed"]

    for preferred_type in preferred_types:
        match = next((f for f in completed_files if f.get("file_type") == preferred_type), None)
        if match:
            return match

    return completed_files[0] if completed_files else None


def _select_zoom_transcript_file(recording_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    transcript_types = {"TRANSCRIPT", "CC"}
    for recording_file in recording_files:
        extension = (recording_file.get("file_extension") or "").lower()
        recording_type = (recording_file.get("recording_type") or "").lower()
        file_type = (recording_file.get("file_type") or "").upper()

        if file_type in transcript_types or extension in {"vtt", "txt"} or "transcript" in recording_type:
            return recording_file

    return None


def _persist_zoom_transcript_file(external_id: str, extension: str, content: str) -> str:
    transcript_dir = os.path.join("uploads", "zoom", "transcripts")
    os.makedirs(transcript_dir, exist_ok=True)
    safe_external_id = re.sub(r"[^A-Za-z0-9_.-]", "_", external_id)[:120] or "zoom_transcript"
    file_path = os.path.join(transcript_dir, f"{safe_external_id}.{extension or 'txt'}")

    with open(file_path, "w", encoding="utf-8") as transcript_file:
        transcript_file.write(content)

    return file_path


def _save_zoom_transcript(
    db: Session,
    meeting: Meeting,
    transcript_text: str,
    source_path: str,
) -> int:
    existing_transcripts = db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting.id)
    ).scalars().all()

    has_non_zoom_transcripts = any(
        (transcript.transcript_metadata or {}).get("source") != "zoom_recording_transcript"
        for transcript in existing_transcripts
    )
    if has_non_zoom_transcripts:
        return 0

    for transcript in existing_transcripts:
        db.delete(transcript)

    segments = _parse_zoom_transcript_segments(transcript_text)
    for index, segment in enumerate(segments):
        db.add(
            Transcript(
                meeting_id=meeting.id,
                segment_number=index,
                speaker_name="Zoom",
                text=segment["text"],
                start_time=segment["start_time"],
                end_time=segment["end_time"],
                duration=max(segment["end_time"] - segment["start_time"], 0.0),
                confidence=1.0,
                transcript_metadata={
                    "source": "zoom_recording_transcript",
                    "source_path": source_path,
                },
            )
        )

    if segments:
        meeting.transcription_status = "completed"

    return len(segments)


async def _get_zoom_access_token(db: Session, current_user: User) -> str:
    zoom = _get_integration(current_user, "zoom")
    account_id = zoom.get("account_id")
    client_id = zoom.get("client_id")
    client_secret = zoom.get("client_secret")
    access_token = zoom.get("access_token")

    expires_at_raw = zoom.get("token_expires_at")
    expires_at = _parse_iso_datetime(expires_at_raw) if isinstance(expires_at_raw, str) else None
    if access_token and expires_at and expires_at > datetime.utcnow() + timedelta(seconds=60):
        return access_token

    if account_id and client_id and client_secret:
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://zoom.us/oauth/token",
                params={"grant_type": "account_credentials", "account_id": account_id},
                headers={"Authorization": f"Basic {credentials}"},
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Zoom authentication failed: {resp.text}")

        token_data = resp.json()
        if "error" in token_data:
            raise HTTPException(
                status_code=400,
                detail=f"Zoom error: {token_data.get('error_description', token_data['error'])}",
            )

        new_token = token_data.get("access_token")
        if not new_token:
            raise HTTPException(status_code=400, detail="Zoom authentication failed: missing access token")

        expires_in = token_data.get("expires_in")
        try:
            expires_in_seconds = max(int(expires_in), 60)
        except (TypeError, ValueError):
            expires_in_seconds = 3600

        zoom["access_token"] = new_token
        zoom["token_expires_at"] = (
            datetime.utcnow() + timedelta(seconds=expires_in_seconds - 30)
        ).replace(microsecond=0).isoformat()
        _save_integration(db, current_user, "zoom", zoom)
        return new_token

    if access_token:
        return access_token

    raise HTTPException(status_code=400, detail="Zoom is not connected")


async def _get_microsoft_access_token(db: Session, current_user: User) -> str:
    ms = _get_integration(current_user, "microsoft")
    tenant_id = ms.get("tenant_id")
    client_id = ms.get("client_id")
    client_secret = ms.get("client_secret")
    access_token = ms.get("access_token")

    expires_at_raw = ms.get("token_expires_at")
    expires_at = _parse_iso_datetime(expires_at_raw) if isinstance(expires_at_raw, str) else None
    if access_token and expires_at and expires_at > datetime.utcnow() + timedelta(seconds=60):
        return access_token

    if tenant_id and client_id and client_secret:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Microsoft authentication failed: {resp.text}")

        token_data = resp.json()
        if "error" in token_data:
            raise HTTPException(
                status_code=400,
                detail=f"Microsoft error: {token_data.get('error_description', token_data['error'])}",
            )

        new_token = token_data.get("access_token")
        if not new_token:
            raise HTTPException(status_code=400, detail="Microsoft authentication failed: missing access token")

        expires_in = token_data.get("expires_in")
        try:
            expires_in_seconds = max(int(expires_in), 60)
        except (TypeError, ValueError):
            expires_in_seconds = 3600

        ms["access_token"] = new_token
        ms["token_expires_at"] = (
            datetime.utcnow() + timedelta(seconds=expires_in_seconds - 30)
        ).replace(microsecond=0).isoformat()
        _save_integration(db, current_user, "microsoft", ms)
        return new_token

    if access_token:
        return access_token

    raise HTTPException(status_code=400, detail="Microsoft is not connected")


def _get_google_oauth_client_credentials(
    google_integration: Dict[str, Any],
    override_client_id: Optional[str] = None,
    override_client_secret: Optional[str] = None,
) -> tuple[str, str]:
    client_id = (override_client_id or google_integration.get("client_id") or settings.GOOGLE_CLIENT_ID or "").strip()
    client_secret = (
        override_client_secret or google_integration.get("client_secret") or settings.GOOGLE_CLIENT_SECRET or ""
    ).strip()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth requires client_id and client_secret (set in integration form or backend settings).",
        )

    return client_id, client_secret


async def _get_google_access_token(db: Session, current_user: User) -> str:
    google = _get_integration(current_user, "google")
    access_token = google.get("access_token")
    refresh_token = google.get("refresh_token") or google.get("oauth_refresh_token")

    expires_at_raw = google.get("token_expires_at")
    expires_at = _parse_iso_datetime(expires_at_raw) if isinstance(expires_at_raw, str) else None
    if access_token and expires_at and expires_at > datetime.utcnow() + timedelta(seconds=60):
        return access_token

    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google OAuth token not configured")

    client_id, client_secret = _get_google_oauth_client_credentials(google)
    redirect_uri = google.get("redirect_uri") or settings.GOOGLE_REDIRECT_URI

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token refresh failed: {resp.text}")

    data = resp.json()
    new_access_token = data.get("access_token")
    if not new_access_token:
        raise HTTPException(status_code=400, detail="Google token refresh failed: missing access_token")

    expires_in = data.get("expires_in")
    try:
        expires_seconds = max(int(expires_in), 60)
    except (TypeError, ValueError):
        expires_seconds = 3600

    google["access_token"] = new_access_token
    google["token_expires_at"] = (
        datetime.utcnow() + timedelta(seconds=expires_seconds - 30)
    ).replace(microsecond=0).isoformat()
    google["method"] = "oauth"
    google["client_id"] = client_id
    google["client_secret"] = client_secret
    _save_integration(db, current_user, "google", google)
    return new_access_token


def _upsert_external_meeting(
    db: Session,
    current_user: User,
    platform: str,
    external_id: str,
    title: str,
    description: Optional[str],
    scheduled_start: Optional[datetime],
    scheduled_end: Optional[datetime],
    meeting_url: Optional[str],
    metadata: Dict[str, Any],
    actual_start: Optional[datetime] = None,
    actual_end: Optional[datetime] = None,
    status: str = "scheduled",
    recording_url: Optional[str] = None,
    recording_size_mb: Optional[float] = None,
    attendee_count: Optional[int] = None,
    recording_consent: Optional[bool] = None,
) -> tuple[str, Optional[Meeting]]:
    if not external_id or not scheduled_start or not scheduled_end:
        return "skipped", None

    existing = db.execute(
        select(Meeting).where(
            Meeting.organizer_id == current_user.id,
            Meeting.platform == platform,
            Meeting.external_id == external_id,
        )
    ).scalar_one_or_none()

    if existing:
        changed = False

        if existing.title != title:
            existing.title = title
            changed = True
        if existing.description != description:
            existing.description = description
            changed = True
        if existing.scheduled_start != scheduled_start:
            existing.scheduled_start = scheduled_start
            changed = True
        if existing.scheduled_end != scheduled_end:
            existing.scheduled_end = scheduled_end
            changed = True
        if existing.meeting_url != meeting_url:
            existing.meeting_url = meeting_url
            changed = True
        if actual_start and existing.actual_start != actual_start:
            existing.actual_start = actual_start
            changed = True
        if actual_end and existing.actual_end != actual_end:
            existing.actual_end = actual_end
            changed = True
        if status and existing.status != status:
            existing.status = status
            changed = True
        if recording_url and existing.recording_url != recording_url:
            existing.recording_url = recording_url
            changed = True
        if recording_size_mb is not None and existing.recording_size_mb != recording_size_mb:
            existing.recording_size_mb = recording_size_mb
            changed = True
        if attendee_count is not None and existing.attendee_count != attendee_count:
            existing.attendee_count = attendee_count
            changed = True
        if recording_consent is not None and existing.recording_consent != recording_consent:
            existing.recording_consent = recording_consent
            changed = True

        merged_metadata = dict(existing.meeting_metadata or {})
        merged_metadata.update(metadata)
        if existing.meeting_metadata != merged_metadata:
            existing.meeting_metadata = merged_metadata
            changed = True

        return ("updated" if changed else "skipped"), existing

    meeting = Meeting(
        title=title,
        description=description,
        meeting_type="external",
        platform=platform,
        external_id=external_id,
        meeting_url=meeting_url,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        actual_start=actual_start,
        actual_end=actual_end,
        organizer_id=current_user.id,
        attendee_ids=[],
        attendee_count=attendee_count or 0,
        status=status,
        recording_url=recording_url,
        recording_size_mb=recording_size_mb,
        recording_consent=recording_consent if recording_consent is not None else False,
        meeting_metadata=metadata,
    )
    db.add(meeting)
    db.flush()
    return "created", meeting


# ─────────────────── List all integrations ───────────────────

@router.get("/")
async def list_integrations(
    current_user: User = Depends(get_current_user),
):
    slack = _get_integration(current_user, "slack")
    linear = _get_integration(current_user, "linear")
    zoom = _get_integration(current_user, "zoom")
    google = _get_integration(current_user, "google")
    microsoft = _get_integration(current_user, "microsoft")

    google_connected = bool(
        google.get("api_key")
        or google.get("service_account_json")
        or google.get("refresh_token")
        or google.get("oauth_refresh_token")
        or google.get("access_token")
    )

    return [
        {
            "id": "slack",
            "name": "Slack",
            "type": "communication",
            "description": "Post meeting summaries, mention alerts and action item reminders to Slack channels.",
            "connected": bool(slack.get("bot_token")),
            "config": {"channel": slack.get("default_channel"), "token_preview": _mask(slack.get("bot_token", "")), "team_name": slack.get("team_name")} if slack.get("bot_token") else None,
        },
        {
            "id": "linear",
            "name": "Linear",
            "type": "project_management",
            "description": "Automatically create Linear issues from action items extracted in meetings.",
            "connected": bool(linear.get("api_key")),
            "config": {"key_preview": _mask(linear.get("api_key", "")), "org_name": linear.get("org_name")} if linear.get("api_key") else None,
        },
        {
            "id": "zoom",
            "name": "Zoom",
            "type": "video",
            "description": "Auto-join Zoom meetings, pull recordings and transcripts directly.",
            "connected": bool(zoom.get("account_id")),
            "config": {"account_id": zoom.get("account_id"), "client_id_preview": _mask(zoom.get("client_id", ""))} if zoom.get("account_id") else None,
        },
        {
            "id": "google",
            "name": "Google Meet & Calendar",
            "type": "calendar",
            "description": "Connect Google OAuth, sync Google Meet events from Calendar, and enable automated capture workflows.",
            "connected": google_connected,
            "config": {
                "calendar_id": google.get("calendar_id"),
                "method": google.get("method") or ("oauth" if google.get("refresh_token") else None),
            }
            if google_connected
            else None,
        },
        {
            "id": "microsoft",
            "name": "Microsoft Teams",
            "type": "calendar",
            "description": "Sync Outlook calendar and Microsoft Teams meetings automatically.",
            "connected": bool(microsoft.get("tenant_id")),
            "config": {"tenant_id": microsoft.get("tenant_id"), "calendar_user": microsoft.get("calendar_user")} if microsoft.get("tenant_id") else None,
        },
    ]


# ─────────────────── SLACK ───────────────────

@router.post("/slack/connect")
async def connect_slack(
    req: SlackConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not req.bot_token.startswith("xoxb-"):
        raise HTTPException(status_code=400, detail="Invalid Slack Bot Token. It must start with 'xoxb-'")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {req.bot_token}"},
        )
    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(status_code=400, detail=f"Slack rejected the token: {data.get('error', 'unknown error')}")

    forced_channel = _forced_slack_channel()
    target_channel = forced_channel or req.default_channel or "#testing"

    _save_integration(db, current_user, "slack", {
        "bot_token": req.bot_token,
        "default_channel": target_channel,
        "webhook_url": req.webhook_url,
        "team_name": data.get("team"),
        "bot_name": data.get("user"),
    })
    return {"status": "connected", "team": data.get("team"), "bot": data.get("user"), "channel": target_channel}


@router.delete("/slack/disconnect")
async def disconnect_slack(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _remove_integration(db, current_user, "slack")
    return {"status": "disconnected"}


class SlackTestRequest(BaseModel):
    channel: Optional[str] = None


@router.post("/slack/test")
async def test_slack(
    req: Optional[SlackTestRequest] = None,
    current_user: User = Depends(get_current_user),
):
    slack = _get_integration(current_user, "slack")
    if not slack.get("bot_token"):
        raise HTTPException(status_code=400, detail="Slack is not connected")

    forced_channel = _forced_slack_channel()
    channel_input = (forced_channel or ((req.channel if req else None) or slack.get("default_channel", "#testing"))).strip()
    channel = channel_input

    async with httpx.AsyncClient() as client:
        if channel_input.startswith("#"):
            channel_name = channel_input[1:]
            list_resp = await client.get(
                "https://slack.com/api/conversations.list",
                headers={"Authorization": f"Bearer {slack['bot_token']}"},
                params={
                    "exclude_archived": "true",
                    "limit": 1000,
                    "types": "public_channel,private_channel",
                },
            )
            list_data = list_resp.json()

            if list_data.get("ok"):
                match = next(
                    (c for c in list_data.get("channels", []) if c.get("name") == channel_name),
                    None,
                )
                if match and match.get("id"):
                    channel = match["id"]
            elif list_data.get("error") == "missing_scope":
                needed = list_data.get("needed", "")
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Slack token cannot resolve channel names. "
                        f"Needed scope: {needed or 'unknown'}. "
                        "Use channel ID (C... or G...) in the test dialog, or re-install the app with required scopes."
                    ),
                )
    
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {slack['bot_token']}"},
            json={
                "channel": channel,
                "text": f":white_check_mark: Meeting Intelligence Agent connected! (test from {current_user.full_name})",
            },
        )
    data = resp.json()
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        if error == "channel_not_found":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Slack test failed: {error}. "
                    "If this is a private channel, use its channel ID (starts with G) instead of #name."
                ),
            )
        raise HTTPException(status_code=400, detail=f"Slack test failed: {error}")
    return {"status": "ok", "message": f"Test message sent to {channel_input}"}


# ─────────────────── LINEAR ───────────────────

@router.post("/linear/connect")
async def connect_linear(
    req: LinearConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = '{ viewer { id name email organization { name } } }'
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": req.api_key, "Content-Type": "application/json"},
            json={"query": query},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Could not reach Linear API. Check your key.")

    data = resp.json()
    if "errors" in data:
        raise HTTPException(status_code=400, detail=f"Linear API error: {data['errors'][0].get('message')}")

    viewer = data.get("data", {}).get("viewer", {})
    org = viewer.get("organization", {})

    _save_integration(db, current_user, "linear", {
        "api_key": req.api_key,
        "user_name": viewer.get("name"),
        "org_name": org.get("name"),
    })
    return {"status": "connected", "user": viewer.get("name"), "organization": org.get("name")}


@router.delete("/linear/disconnect")
async def disconnect_linear(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _remove_integration(db, current_user, "linear")
    return {"status": "disconnected"}


@router.post("/linear/test")
async def test_linear(
    current_user: User = Depends(get_current_user),
):
    linear = _get_integration(current_user, "linear")
    if not linear.get("api_key"):
        raise HTTPException(status_code=400, detail="Linear is not connected")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": linear["api_key"], "Content-Type": "application/json"},
            json={"query": '{ teams { nodes { id name } } }'},
        )
    data = resp.json()
    teams = data.get("data", {}).get("teams", {}).get("nodes", [])
    return {"status": "ok", "teams": [t["name"] for t in teams]}


# ─────────────────── ZOOM ───────────────────

@router.post("/zoom/connect")
async def connect_zoom(
    req: ZoomConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    credentials = base64.b64encode(f"{req.client_id}:{req.client_secret}".encode()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": req.account_id},
            headers={"Authorization": f"Basic {credentials}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Zoom authentication failed: {resp.text}")

    token_data = resp.json()
    if "error" in token_data:
        raise HTTPException(status_code=400, detail=f"Zoom error: {token_data.get('error_description', token_data['error'])}")

    _save_integration(db, current_user, "zoom", {
        "account_id": req.account_id,
        "client_id": req.client_id,
        "client_secret": req.client_secret,
        "access_token": token_data.get("access_token"),
        "token_expires_at": (
            datetime.utcnow() + timedelta(seconds=max(int(token_data.get("expires_in", 3600)), 60) - 30)
        ).replace(microsecond=0).isoformat(),
    })
    return {"status": "connected", "account_id": req.account_id}


@router.delete("/zoom/disconnect")
async def disconnect_zoom(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _remove_integration(db, current_user, "zoom")
    return {"status": "disconnected"}


@router.post("/zoom/test")
async def test_zoom(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    access_token = await _get_zoom_access_token(db, current_user)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.zoom.us/v2/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Zoom test failed: {resp.text}")

    data = resp.json()
    return {
        "status": "ok",
        "user": data.get("display_name") or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
        "email": data.get("email"),
    }


@router.get("/zoom/meetings")
async def list_zoom_meetings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch upcoming Zoom meetings for the connected account."""
    access_token = await _get_zoom_access_token(db, current_user)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.zoom.us/v2/users/me/meetings",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"type": "upcoming", "page_size": 10},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=_format_zoom_api_error(resp, "Failed to fetch Zoom meetings"),
        )

    data = resp.json()
    meetings = data.get("meetings", [])
    return [
        {
            "id": m.get("id"),
            "topic": m.get("topic"),
            "start_time": m.get("start_time"),
            "duration": m.get("duration"),
            "join_url": m.get("join_url"),
        }
        for m in meetings
    ]


@router.post("/zoom/sync")
async def sync_zoom_meetings(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sync upcoming Zoom meetings into internal meetings table."""
    access_token = await _get_zoom_access_token(db, current_user)

    page_size = max(1, min(limit, 100))

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.zoom.us/v2/users/me/meetings",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"type": "upcoming", "page_size": page_size},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=_format_zoom_api_error(resp, "Failed to fetch Zoom meetings"),
        )

    meetings = resp.json().get("meetings", [])
    capture_policy = _get_capture_policy(current_user)
    created = 0
    updated = 0
    skipped = 0

    for meeting in meetings:
        external_id = str(meeting.get("id") or "")
        start_time = _parse_iso_datetime(meeting.get("start_time"))

        duration_minutes = meeting.get("duration")
        try:
            duration_minutes = int(duration_minutes)
        except (TypeError, ValueError):
            duration_minutes = 30

        end_time = start_time + timedelta(minutes=duration_minutes) if start_time else None
        policy_metadata = _apply_capture_policy_metadata(
            capture_policy,
            meeting.get("topic") or "Zoom Meeting",
            meeting.get("agenda"),
            attendee_count=0,
            tags=[],
            platform="zoom",
            scheduled_start=start_time,
        )
        recording_consent = not bool(capture_policy.get("require_explicit_consent", True))

        result, _ = _upsert_external_meeting(
            db=db,
            current_user=current_user,
            platform="zoom",
            external_id=external_id,
            title=meeting.get("topic") or "Zoom Meeting",
            description=meeting.get("agenda"),
            scheduled_start=start_time,
            scheduled_end=end_time,
            meeting_url=meeting.get("join_url"),
            metadata={
                "source": "zoom",
                "timezone": meeting.get("timezone"),
                "zoom_type": meeting.get("type"),
                "host_id": meeting.get("host_id"),
                **policy_metadata,
            },
            status="scheduled",
            attendee_count=0,
            recording_consent=recording_consent,
        )

        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        else:
            skipped += 1

    db.commit()

    return {
        "status": "ok",
        "platform": "zoom",
        "fetched": len(meetings),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }


@router.post("/zoom/import-history")
async def import_zoom_history(
    days_back: int = 30,
    limit: int = 20,
    import_recordings: bool = True,
    import_transcripts: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import past Zoom sessions from cloud recordings, including transcript data when available."""
    access_token = await _get_zoom_access_token(db, current_user)

    safe_days_back = max(1, min(days_back, 180))
    page_size = max(1, min(limit, 100))
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=safe_days_back)

    imported = 0
    updated = 0
    skipped = 0
    recordings_found = 0
    transcript_segments = 0
    transcripts_imported = 0
    next_page_token: Optional[str] = None
    imported_rows = 0
    capture_policy = _get_capture_policy(current_user)

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        while imported_rows < limit:
            params = {
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "page_size": page_size,
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            resp = await client.get(
                "https://api.zoom.us/v2/users/me/recordings",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=_format_zoom_api_error(resp, "Failed to fetch Zoom recordings"),
                )

            payload = resp.json()
            meetings = payload.get("meetings", [])

            for meeting in meetings:
                if imported_rows >= limit:
                    break

                recording_files = meeting.get("recording_files", [])
                primary_recording = _select_zoom_recording_file(recording_files)
                transcript_file = _select_zoom_transcript_file(recording_files)

                if primary_recording:
                    recordings_found += 1

                external_id = str(meeting.get("uuid") or meeting.get("id") or "")
                start_time = _parse_iso_datetime(meeting.get("start_time"))

                duration_minutes = meeting.get("duration")
                try:
                    duration_minutes = int(duration_minutes)
                except (TypeError, ValueError):
                    duration_minutes = 30

                end_time = start_time + timedelta(minutes=duration_minutes) if start_time else None
                recording_size_mb = None
                recording_url = None

                if primary_recording and import_recordings:
                    file_size = primary_recording.get("file_size")
                    if isinstance(file_size, (int, float)):
                        recording_size_mb = round(float(file_size) / (1024 * 1024), 2)
                    recording_url = primary_recording.get("download_url") or primary_recording.get("play_url")

                result, meeting_record = _upsert_external_meeting(
                    db=db,
                    current_user=current_user,
                    platform="zoom",
                    external_id=external_id,
                    title=meeting.get("topic") or "Zoom Meeting",
                    description=meeting.get("agenda"),
                    scheduled_start=start_time,
                    scheduled_end=end_time,
                    meeting_url=meeting.get("share_url") or meeting.get("join_url"),
                    metadata={
                        "source": "zoom_recording",
                        "zoom_uuid": meeting.get("uuid"),
                        "timezone": meeting.get("timezone"),
                        "host_id": meeting.get("host_id"),
                        "recording_count": len(recording_files),
                        "imported_from": "zoom_history",
                        **_apply_capture_policy_metadata(
                            capture_policy,
                            meeting.get("topic") or "Zoom Meeting",
                            meeting.get("agenda"),
                            attendee_count=0,
                            tags=[],
                            platform="zoom",
                            scheduled_start=start_time,
                        ),
                    },
                    actual_start=start_time,
                    actual_end=end_time,
                    status="completed",
                    recording_url=recording_url,
                    recording_size_mb=recording_size_mb,
                    attendee_count=0,
                    recording_consent=not bool(capture_policy.get("require_explicit_consent", True)),
                )

                if result == "created":
                    imported += 1
                elif result == "updated":
                    updated += 1
                else:
                    skipped += 1

                if meeting_record and import_transcripts and transcript_file and transcript_file.get("download_url"):
                    transcript_response = await client.get(
                        transcript_file["download_url"],
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if transcript_response.status_code == 200:
                        transcript_extension = (transcript_file.get("file_extension") or "vtt").lower()
                        raw_transcript = transcript_response.text
                        transcript_path = _persist_zoom_transcript_file(external_id, transcript_extension, raw_transcript)
                        saved_segments = _save_zoom_transcript(db, meeting_record, raw_transcript, transcript_path)
                        if saved_segments:
                            transcripts_imported += 1
                            transcript_segments += saved_segments
                            metadata = dict(meeting_record.meeting_metadata or {})
                            metadata.update(
                                {
                                    "transcript_path": transcript_path,
                                    "transcript_file_type": transcript_file.get("file_type"),
                                }
                            )
                            meeting_record.meeting_metadata = metadata

                imported_rows += 1

            next_page_token = payload.get("next_page_token")
            if not next_page_token or not meetings:
                break

    db.commit()

    return {
        "status": "ok",
        "platform": "zoom",
        "days_back": safe_days_back,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "recordings_found": recordings_found,
        "transcripts_imported": transcripts_imported,
        "transcript_segments": transcript_segments,
    }


# ─────────────────── GOOGLE CALENDAR ───────────────────

@router.post("/google/connect")
async def connect_google(
    req: GoogleConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    has_api_key = bool(req.api_key)
    has_service_account = bool(req.service_account_json)
    has_oauth_refresh_token = bool(req.oauth_refresh_token)

    if not (has_api_key or has_service_account or has_oauth_refresh_token):
        raise HTTPException(
            status_code=400,
            detail="Provide API key, service account JSON, or OAuth refresh token.",
        )

    google_payload: Dict[str, Any] = {
        "api_key": req.api_key,
        "calendar_id": req.calendar_id or "primary",
        "service_account_json": req.service_account_json,
        "refresh_token": req.oauth_refresh_token,
        "client_id": req.client_id or settings.GOOGLE_CLIENT_ID,
        "client_secret": req.client_secret or settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "method": "oauth" if has_oauth_refresh_token else ("service_account" if has_service_account else "api_key"),
    }

    _save_integration(db, current_user, "google", google_payload)
    return {
        "status": "connected",
        "calendar_id": req.calendar_id or "primary",
        "method": google_payload["method"],
    }


@router.get("/google/oauth-url")
async def google_oauth_url(
    redirect_uri: Optional[str] = None,
    calendar_id: str = "primary",
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    google = _get_integration(current_user, "google")
    resolved_client_id = (client_id or google.get("client_id") or settings.GOOGLE_CLIENT_ID or "").strip()
    resolved_redirect_uri = (redirect_uri or settings.GOOGLE_REDIRECT_URI or google.get("redirect_uri") or "").strip()

    if not resolved_client_id:
        raise HTTPException(status_code=400, detail="Missing Google OAuth client_id")
    if not resolved_redirect_uri:
        raise HTTPException(status_code=400, detail="Missing Google OAuth redirect_uri")

    state = uuid.uuid4().hex
    query = urlencode(
        {
            "client_id": resolved_client_id,
            "response_type": "code",
            "redirect_uri": resolved_redirect_uri,
            "scope": (
                "https://www.googleapis.com/auth/calendar.readonly "
                "https://www.googleapis.com/auth/calendar.events.readonly "
                "https://www.googleapis.com/auth/meetings.space.readonly "
                "openid email profile"
            ),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    google["oauth_state"] = state
    google["calendar_id"] = calendar_id or "primary"
    google["client_id"] = resolved_client_id
    google["redirect_uri"] = resolved_redirect_uri
    _save_integration(db, current_user, "google", google)

    return {"status": "ok", "url": url, "state": state}


@router.post("/google/oauth/callback")
async def google_oauth_callback(
    req: GoogleOAuthCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    google = _get_integration(current_user, "google")
    client_id, client_secret = _get_google_oauth_client_credentials(
        google,
        override_client_id=req.client_id,
        override_client_secret=req.client_secret,
    )
    redirect_uri = (req.redirect_uri or settings.GOOGLE_REDIRECT_URI or google.get("redirect_uri") or "").strip()
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="Google OAuth callback requires redirect_uri")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": req.code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google OAuth exchange failed: {resp.text}")

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token") or google.get("refresh_token") or google.get("oauth_refresh_token")
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth exchange failed: missing access_token",
        )

    try:
        expires_seconds = max(int(data.get("expires_in", 3600)), 60)
    except (TypeError, ValueError):
        expires_seconds = 3600

    google.update(
        {
            "method": "oauth",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "oauth_refresh_token": refresh_token,
            "token_expires_at": (
                datetime.utcnow() + timedelta(seconds=expires_seconds - 30)
            ).replace(microsecond=0).isoformat(),
            "calendar_id": req.calendar_id or google.get("calendar_id") or "primary",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
    )
    _save_integration(db, current_user, "google", google)

    return {
        "status": "connected",
        "method": "oauth",
        "calendar_id": google.get("calendar_id") or "primary",
    }


@router.delete("/google/disconnect")
async def disconnect_google(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _remove_integration(db, current_user, "google")
    return {"status": "disconnected"}


@router.post("/google/test")
async def test_google(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    google = _get_integration(current_user, "google")
    api_key = google.get("api_key")
    calendar_id = google.get("calendar_id") or "primary"
    service_account_json = google.get("service_account_json")
    oauth_refresh_token = google.get("refresh_token")

    if not api_key and not service_account_json and not oauth_refresh_token:
        raise HTTPException(status_code=400, detail="Google is not connected")

    if not api_key and service_account_json:
        try:
            parsed = json.loads(service_account_json)
            client_email = parsed.get("client_email")
            private_key = parsed.get("private_key")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid service account JSON: {exc}")

        if not client_email or not private_key:
            raise HTTPException(
                status_code=400,
                detail="Service account JSON is missing required fields (client_email/private_key)",
            )

        return {
            "status": "ok",
            "method": "service_account",
            "calendar_id": calendar_id,
            "service_account": client_email,
            "note": "Service account credentials look valid. Share the target calendar with this service account email.",
        }

    if oauth_refresh_token:
        access_token = await _get_google_access_token(db=db, current_user=current_user)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"maxResults": 1},
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Google OAuth test failed: {resp.text}")

        return {
            "status": "ok",
            "method": "oauth",
            "calendar_id": calendar_id,
            "summary": "OAuth token valid",
        }

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}",
            params={"key": api_key},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google test failed: {resp.text}")

    data = resp.json()
    return {
        "status": "ok",
        "method": "api_key",
        "calendar_id": data.get("id") or calendar_id,
        "summary": data.get("summary") or "Unknown",
        "time_zone": data.get("timeZone"),
    }


@router.get("/google/calendars")
async def list_google_calendars(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all Google Calendars accessible to the authenticated user."""
    from app.services.integrations.google_meet import google_meet_service

    google = _get_integration(current_user, "google")
    if not google.get("refresh_token") and not google.get("access_token"):
        raise HTTPException(status_code=400, detail="Google is not connected via OAuth")

    access_token = await _get_google_access_token(db=db, current_user=current_user)
    calendars = await google_meet_service.list_calendars(access_token)
    return {"calendars": calendars}


@router.get("/google/meet/spaces")
async def list_google_meet_spaces(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List Google Meet spaces (requires meetings.space.readonly scope).
    Re-connect Google OAuth if this returns a 403 to grant the new scope.
    """
    from app.services.integrations.google_meet import google_meet_service

    google = _get_integration(current_user, "google")
    if not google.get("refresh_token") and not google.get("access_token"):
        raise HTTPException(status_code=400, detail="Google is not connected via OAuth")

    access_token = await _get_google_access_token(db=db, current_user=current_user)
    spaces = await google_meet_service.list_spaces(access_token)
    return {"spaces": spaces, "count": len(spaces)}


@router.get("/google/meet/spaces/{space_id}/recordings")
async def list_google_meet_recordings(
    space_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List recordings for a specific Google Meet space."""
    from app.services.integrations.google_meet import google_meet_service

    google = _get_integration(current_user, "google")
    if not google.get("refresh_token") and not google.get("access_token"):
        raise HTTPException(status_code=400, detail="Google is not connected via OAuth")

    access_token = await _get_google_access_token(db=db, current_user=current_user)
    space_name = f"spaces/{space_id}"
    recordings = await google_meet_service.list_recordings(access_token, space_name)
    return {"space": space_name, "recordings": recordings, "count": len(recordings)}


@router.get("/google/meet/spaces/{space_id}/transcripts")
async def list_google_meet_transcripts(
    space_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List transcripts for a specific Google Meet space."""
    from app.services.integrations.google_meet import google_meet_service

    google = _get_integration(current_user, "google")
    if not google.get("refresh_token") and not google.get("access_token"):
        raise HTTPException(status_code=400, detail="Google is not connected via OAuth")

    access_token = await _get_google_access_token(db=db, current_user=current_user)
    space_name = f"spaces/{space_id}"
    transcripts = await google_meet_service.list_transcripts(access_token, space_name)
    return {"space": space_name, "transcripts": transcripts, "count": len(transcripts)}


@router.get("/google/meet/spaces/{space_id}/transcripts/{transcript_id}/entries")
async def list_google_meet_transcript_entries(
    space_id: str,
    transcript_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List transcript entries (utterances) for a specific transcript."""
    from app.services.integrations.google_meet import google_meet_service

    google = _get_integration(current_user, "google")
    if not google.get("refresh_token") and not google.get("access_token"):
        raise HTTPException(status_code=400, detail="Google is not connected via OAuth")

    access_token = await _get_google_access_token(db=db, current_user=current_user)
    transcript_name = f"spaces/{space_id}/transcripts/{transcript_id}"
    entries = await google_meet_service.list_transcript_entries(access_token, transcript_name)
    return {"transcript": transcript_name, "entries": entries, "count": len(entries)}


@router.get("/google/meet/upcoming")
async def list_upcoming_google_meet_events(
    days_ahead: int = 30,
    calendar_id: str = "primary",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List upcoming calendar events that have a Google Meet link.
    Useful for showing what meetings are coming up before syncing.
    """
    from app.services.integrations.google_meet import google_meet_service

    google = _get_integration(current_user, "google")
    if not google.get("refresh_token") and not google.get("access_token") and not google.get("api_key"):
        raise HTTPException(status_code=400, detail="Google is not connected")

    resolved_calendar_id = calendar_id or google.get("calendar_id") or "primary"

    if google.get("refresh_token") or google.get("access_token"):
        access_token = await _get_google_access_token(db=db, current_user=current_user)
        events = await google_meet_service.list_upcoming_meet_events(
            access_token=access_token,
            calendar_id=resolved_calendar_id,
            days_ahead=min(days_ahead, 180),
        )
    else:
        # API key path — use existing sync logic
        raise HTTPException(
            status_code=400,
            detail="Listing upcoming Meet events requires OAuth (not API key). Please reconnect with OAuth.",
        )

    return {"events": events, "count": len(events), "calendar_id": resolved_calendar_id}


@router.post("/google/sync")
async def sync_google_meetings(
    days_ahead: int = 30,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sync upcoming Google Calendar events into internal meetings table."""
    google = _get_integration(current_user, "google")
    api_key = google.get("api_key")
    calendar_id = google.get("calendar_id") or "primary"
    oauth_refresh_token = google.get("refresh_token")

    if not api_key and not oauth_refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Google Meet sync requires API key or OAuth credentials.",
        )

    days = max(1, min(days_ahead, 180))
    page_size = max(1, min(limit, 250))
    now_utc = datetime.now(timezone.utc)
    end_utc = now_utc + timedelta(days=days)

    request_params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": page_size,
        "timeMin": now_utc.isoformat().replace("+00:00", "Z"),
        "timeMax": end_utc.isoformat().replace("+00:00", "Z"),
    }

    request_headers: Dict[str, str] = {}
    if api_key:
        request_params["key"] = api_key
    else:
        access_token = await _get_google_access_token(db, current_user)
        request_headers["Authorization"] = f"Bearer {access_token}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            params=request_params,
            headers=request_headers,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Failed to fetch Google events: {resp.text}")

    events = resp.json().get("items", [])
    capture_policy = _get_capture_policy(current_user)
    created = 0
    updated = 0
    skipped = 0
    meet_events = 0

    for event in events:
        external_id = event.get("id") or ""
        start_data = event.get("start") or {}
        end_data = event.get("end") or {}

        start_time = _parse_google_datetime(start_data.get("dateTime"), start_data.get("date"))
        end_time = _parse_google_datetime(end_data.get("dateTime"), end_data.get("date"))
        conference_data = event.get("conferenceData") or {}
        entry_points = conference_data.get("entryPoints") or []
        meet_entry = next(
            (
                entry
                for entry in entry_points
                if entry.get("entryPointType") == "video" and "meet.google.com" in str(entry.get("uri") or "")
            ),
            None,
        )
        meeting_url = event.get("hangoutLink") or (meet_entry or {}).get("uri")
        if not meeting_url:
            skipped += 1
            continue

        meet_events += 1
        attendees = event.get("attendees") or []
        attendee_count = len(attendees)
        tags = event.get("extendedProperties", {}).get("private", {}).get("tags", "")
        tag_list = [t.strip().lower() for t in str(tags).split(",") if t.strip()] if tags else []

        result, _ = _upsert_external_meeting(
            db=db,
            current_user=current_user,
            platform="meet",
            external_id=external_id,
            title=event.get("summary") or "Google Meet Meeting",
            description=event.get("description"),
            scheduled_start=start_time,
            scheduled_end=end_time,
            meeting_url=meeting_url,
            metadata={
                "source": "google_meet",
                "calendar_id": calendar_id,
                "status": event.get("status"),
                "event_type": event.get("eventType"),
                "creator": (event.get("creator") or {}).get("email"),
                "organizer": (event.get("organizer") or {}).get("email"),
                "conference_id": conference_data.get("conferenceId"),
                **_apply_capture_policy_metadata(
                    capture_policy,
                    event.get("summary") or "Google Meet Meeting",
                    event.get("description"),
                    attendee_count=attendee_count,
                    tags=tag_list,
                    platform="meet",
                    scheduled_start=start_time,
                ),
            },
            status="scheduled",
            attendee_count=attendee_count,
            recording_consent=not bool(capture_policy.get("require_explicit_consent", True)),
        )

        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        else:
            skipped += 1

    db.commit()

    return {
        "status": "ok",
        "platform": "google",
        "fetched": len(events),
        "meet_events": meet_events,
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }


# ─────────────────── MICROSOFT TEAMS ───────────────────

@router.post("/microsoft/connect")
async def connect_microsoft(
    req: MicrosoftConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{req.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": req.client_id,
                "client_secret": req.client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Microsoft authentication failed: {resp.text}")

    token_data = resp.json()
    if "error" in token_data:
        raise HTTPException(status_code=400, detail=f"Microsoft error: {token_data.get('error_description', token_data['error'])}")

    _save_integration(db, current_user, "microsoft", {
        "tenant_id": req.tenant_id,
        "client_id": req.client_id,
        "client_secret": req.client_secret,
        "access_token": token_data.get("access_token"),
        "calendar_user": req.calendar_user,
        "token_expires_at": (
            datetime.utcnow() + timedelta(seconds=max(int(token_data.get("expires_in", 3600)), 60) - 30)
        ).replace(microsecond=0).isoformat(),
    })
    return {"status": "connected", "tenant_id": req.tenant_id}


@router.delete("/microsoft/disconnect")
async def disconnect_microsoft(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _remove_integration(db, current_user, "microsoft")
    return {"status": "disconnected"}


@router.post("/microsoft/test")
async def test_microsoft(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    access_token = await _get_microsoft_access_token(db, current_user)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/organization",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Microsoft test failed: {resp.text}")

    data = resp.json()
    orgs = data.get("value", [])
    return {"status": "ok", "organization": orgs[0].get("displayName") if orgs else "Unknown"}


@router.post("/microsoft/sync")
async def sync_microsoft_meetings(
    days_ahead: int = 30,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sync upcoming Microsoft calendar events into internal meetings table."""
    ms = _get_integration(current_user, "microsoft")
    access_token = await _get_microsoft_access_token(db, current_user)

    calendar_user = ms.get("calendar_user")
    if not calendar_user:
        raise HTTPException(
            status_code=400,
            detail="Microsoft sync requires calendar_user. Reconnect Microsoft and set calendar_user (UPN/email).",
        )

    days = max(1, min(days_ahead, 180))
    page_size = max(1, min(limit, 200))
    now_utc = datetime.now(timezone.utc)
    end_utc = now_utc + timedelta(days=days)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{calendar_user}/calendar/events",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Prefer": 'outlook.timezone="UTC"',
            },
            params={
                "$top": page_size,
                "$orderby": "start/dateTime",
                "startDateTime": now_utc.isoformat(),
                "endDateTime": end_utc.isoformat(),
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Failed to fetch Microsoft events: {resp.text}")

    events = resp.json().get("value", [])
    capture_policy = _get_capture_policy(current_user)
    created = 0
    updated = 0
    skipped = 0

    for event in events:
        external_id = event.get("id") or ""
        start_time = _parse_iso_datetime((event.get("start") or {}).get("dateTime"))
        end_time = _parse_iso_datetime((event.get("end") or {}).get("dateTime"))
        join_url = ((event.get("onlineMeeting") or {}).get("joinUrl")) or event.get("webLink")
        attendees = event.get("attendees") or []
        attendee_count = len(attendees)
        categories = event.get("categories") or []

        result, _ = _upsert_external_meeting(
            db=db,
            current_user=current_user,
            platform="microsoft",
            external_id=external_id,
            title=event.get("subject") or "Microsoft Teams Meeting",
            description=event.get("bodyPreview"),
            scheduled_start=start_time,
            scheduled_end=end_time,
            meeting_url=join_url,
            metadata={
                "source": "microsoft",
                "calendar_user": calendar_user,
                "is_online_meeting": event.get("isOnlineMeeting"),
                "organizer": (event.get("organizer") or {}).get("emailAddress"),
                **_apply_capture_policy_metadata(
                    capture_policy,
                    event.get("subject") or "Microsoft Teams Meeting",
                    event.get("bodyPreview"),
                    attendee_count=attendee_count,
                    tags=categories,
                    platform="microsoft",
                    scheduled_start=start_time,
                ),
            },
            attendee_count=attendee_count,
            recording_consent=not bool(capture_policy.get("require_explicit_consent", True)),
        )

        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        else:
            skipped += 1

    db.commit()

    return {
        "status": "ok",
        "platform": "microsoft",
        "fetched": len(events),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }


@router.get("/capture-policy")
async def get_capture_policy(
    current_user: User = Depends(get_current_user),
):
    return _get_capture_policy(current_user)


@router.put("/capture-policy")
async def update_capture_policy(
    req: CapturePolicyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    policy = _save_capture_policy(db, current_user, req.model_dump())
    return {"status": "ok", "policy": policy}


@router.post("/capture-policy/evaluate")
async def evaluate_capture_policy(
    req: CapturePolicyEvaluateRequest,
    current_user: User = Depends(get_current_user),
):
    policy = _get_capture_policy(current_user)
    return {
        "status": "ok",
        "policy": policy,
        "evaluation": _evaluate_capture_policy(
            policy,
            title=req.title,
            description=req.description,
            attendee_count=req.attendee_count,
            tags=req.tags,
            platform=req.platform,
            scheduled_start=_parse_iso_datetime(req.scheduled_start) if req.scheduled_start else None,
        ),
    }


@router.get("/consent/announcement/{meeting_id}")
async def get_consent_announcement(
    meeting_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.organizer_id == current_user.id)
    ).scalar_one_or_none()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    policy = _get_capture_policy(current_user)
    return {
        "status": "ok",
        "meeting_id": str(meeting.id),
        "announcement": _build_consent_announcement(meeting, policy),
        "require_explicit_consent": bool(policy.get("require_explicit_consent", True)),
    }


@router.post("/consent/opt-out/{meeting_id}")
async def opt_out_of_recording(
    meeting_id: str,
    req: MeetingConsentOptOutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.organizer_id == current_user.id)
    ).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    policy = _get_capture_policy(current_user)
    metadata = dict(meeting.meeting_metadata or {})
    opt_out_requests = list(metadata.get("opt_out_requests") or [])
    opt_out_requests.append(
        {
            "attendee_name": req.attendee_name,
            "attendee_email": req.attendee_email,
            "reason": req.reason,
            "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
    )

    metadata["opt_out_requests"] = opt_out_requests
    metadata["no_record_requested"] = True
    metadata["consent_reason"] = req.reason
    metadata["consent_updated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    meeting.meeting_metadata = metadata

    if policy.get("respect_no_record_requests", True):
        meeting.recording_consent = False
        _set_bot_session_metadata(meeting, "blocked", "no_record_requested")

    db.commit()

    return {
        "status": "ok",
        "meeting_id": str(meeting.id),
        "opt_out_count": len(opt_out_requests),
        "recording_consent": bool(meeting.recording_consent),
    }


@router.post("/bots/join/{meeting_id}")
async def join_meeting_with_bot(
    meeting_id: str,
    req: BotJoinRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.organizer_id == current_user.id)
    ).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    policy = _get_capture_policy(current_user)
    if not policy.get("auto_join_enabled", True) and not req.force:
        raise HTTPException(status_code=400, detail="Auto-join disabled by capture policy")

    result = _launch_bot_for_meeting(meeting, policy, force=req.force)
    db.commit()
    return {"status": "ok", **result}


@router.post("/bots/auto-join/run-now")
async def run_auto_join_bots_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    policy = _get_capture_policy(current_user)
    if not policy.get("auto_join_enabled", True):
        return {
            "status": "ok",
            "joined": 0,
            "blocked": 0,
            "skipped": 0,
            "message": "Auto-join is disabled by policy",
        }

    now_utc = datetime.utcnow()
    horizon = now_utc + timedelta(minutes=90)
    meetings = db.execute(
        select(Meeting).where(
            Meeting.organizer_id == current_user.id,
            Meeting.platform.in_(["zoom", "meet", "microsoft"]),
            Meeting.scheduled_start >= now_utc - timedelta(minutes=15),
            Meeting.scheduled_start <= horizon,
            Meeting.deleted_at.is_(None),
        )
    ).scalars().all()

    joined = 0
    blocked = 0
    skipped = 0
    details: List[Dict[str, Any]] = []

    for meeting in meetings:
        result = _launch_bot_for_meeting(meeting, policy, force=False)
        details.append({"meeting_id": str(meeting.id), "title": meeting.title, **result})
        if result.get("status") == "joined":
            joined += 1
        elif result.get("status") == "blocked":
            blocked += 1
        else:
            skipped += 1

    db.commit()

    return {
        "status": "ok",
        "joined": joined,
        "blocked": blocked,
        "skipped": skipped,
        "considered": len(meetings),
        "details": details,
    }


@router.post("/live-transcription/{meeting_id}/start")
async def start_live_transcription(
    meeting_id: str,
    req: LiveTranscriptionStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.organizer_id == current_user.id)
    ).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    policy = _get_capture_policy(current_user)
    blocked_reason = _meeting_capture_block_reason(meeting, policy)
    if blocked_reason:
        raise HTTPException(status_code=400, detail=f"Capture blocked: {blocked_reason}")

    metadata = dict(meeting.meeting_metadata or {})
    live = dict(metadata.get("live_transcription") or {})
    live["active"] = True
    live["started_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    live["segments_ingested"] = int(live.get("segments_ingested", 0))
    if req.bot_id:
        live["bot_id"] = req.bot_id
    metadata["live_transcription"] = live
    meeting.meeting_metadata = metadata
    meeting.status = "in_progress"
    meeting.transcription_status = "processing"

    db.commit()

    return {
        "status": "ok",
        "meeting_id": str(meeting.id),
        "live_transcription": live,
    }


@router.post("/live-transcription/{meeting_id}/segment")
async def ingest_live_transcript_segment(
    meeting_id: str,
    req: LiveTranscriptSegmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.organizer_id == current_user.id)
    ).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Segment text is required")

    policy = _get_capture_policy(current_user)
    blocked_reason = _meeting_capture_block_reason(meeting, policy)
    if blocked_reason:
        raise HTTPException(status_code=400, detail=f"Capture blocked: {blocked_reason}")

    current_count = db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting.id)
    ).scalars().all()
    segment_number = len(current_count)

    transcript = Transcript(
        meeting_id=meeting.id,
        segment_number=segment_number,
        speaker_name=req.speaker_name,
        text=req.text.strip(),
        language=req.language,
        start_time=req.start_time,
        end_time=req.end_time,
        duration=max(req.end_time - req.start_time, 0.0),
        confidence=max(0.0, min(req.confidence, 1.0)),
        transcript_metadata={
            "source": "live_stream",
            "is_final": bool(req.is_final),
            "ingested_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        },
    )
    db.add(transcript)
    db.flush()

    metadata = dict(meeting.meeting_metadata or {})
    live = dict(metadata.get("live_transcription") or {})
    live["active"] = True
    live["last_segment_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    live["segments_ingested"] = int(live.get("segments_ingested", 0)) + 1
    metadata["live_transcription"] = live
    meeting.meeting_metadata = metadata
    meeting.transcription_status = "processing"
    if meeting.status == "scheduled":
        meeting.status = "in_progress"

    mentions = await detect_and_store_mentions(
        db=db,
        meeting=meeting,
        transcript_text=req.text.strip(),
        transcript_id=str(transcript.id),
        send_real_time_alerts=bool(req.is_final),
        meeting_context={
            "speaker_name": req.speaker_name,
            "language": req.language,
            "source": "live_stream",
        },
    )

    db.commit()

    return {
        "status": "ok",
        "meeting_id": str(meeting.id),
        "segment_number": segment_number,
        "segments_ingested": live["segments_ingested"],
        "mentions_detected": len(mentions),
    }


@router.post("/live-transcription/{meeting_id}/stop")
async def stop_live_transcription(
    meeting_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.organizer_id == current_user.id)
    ).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    segment_count = len(
        db.execute(select(Transcript).where(Transcript.meeting_id == meeting.id)).scalars().all()
    )

    metadata = dict(meeting.meeting_metadata or {})
    live = dict(metadata.get("live_transcription") or {})
    live["active"] = False
    live["ended_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    live["segments_ingested"] = segment_count
    metadata["live_transcription"] = live
    meeting.meeting_metadata = metadata
    meeting.transcription_status = "completed" if segment_count > 0 else "pending"
    if meeting.status == "in_progress":
        meeting.status = "completed"

    db.commit()

    return {
        "status": "ok",
        "meeting_id": str(meeting.id),
        "segments_ingested": segment_count,
    }


@router.post("/capture-consent/{meeting_id}")
async def set_meeting_capture_consent(
    meeting_id: str,
    req: MeetingConsentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.organizer_id == current_user.id,
        )
    ).scalar_one_or_none()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting.recording_consent = bool(req.recording_consent)
    metadata = dict(meeting.meeting_metadata or {})
    metadata["no_record_requested"] = bool(req.no_record_requested)
    metadata["consent_reason"] = req.reason
    metadata["consent_updated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    meeting.meeting_metadata = metadata
    db.commit()

    return {
        "status": "ok",
        "meeting_id": str(meeting.id),
        "recording_consent": meeting.recording_consent,
        "no_record_requested": metadata.get("no_record_requested", False),
    }


@router.get("/auto-sync/status")
async def get_auto_sync_status(
    current_user: User = Depends(get_current_user),
):
    return _get_auto_sync_state(current_user)


@router.post("/auto-sync/settings")
async def update_auto_sync_settings(
    req: AutoSyncPlatformRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if req.platform not in {"zoom", "google", "microsoft"}:
        raise HTTPException(status_code=400, detail="Unsupported platform for auto sync")

    state = _set_auto_sync_enabled(db, current_user, req.platform, req.enabled)
    return {"status": "ok", **state}


@router.post("/auto-sync/run-now")
async def run_auto_sync_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    results: Dict[str, Any] = {}

    async def _run(platform: str, coro):
        try:
            data = await coro
            results[platform] = data
            _set_auto_sync_status(
                db,
                current_user,
                platform,
                "ok",
                metrics={
                    "created": data.get("created", 0),
                    "updated": data.get("updated", 0),
                    "skipped": data.get("skipped", 0),
                    "fetched": data.get("fetched", 0),
                },
            )
        except HTTPException as exc:
            results[platform] = {"status": "error", "detail": exc.detail}
            _set_auto_sync_status(db, current_user, platform, "error", detail=str(exc.detail))
        except Exception as exc:
            results[platform] = {"status": "error", "detail": str(exc)}
            _set_auto_sync_status(db, current_user, platform, "error", detail=str(exc))

    google = _get_integration(current_user, "google")
    ms = _get_integration(current_user, "microsoft")
    zoom = _get_integration(current_user, "zoom")

    if zoom.get("account_id") and _is_auto_sync_enabled(current_user, "zoom"):
        await _run("zoom", sync_zoom_meetings(limit=20, db=db, current_user=current_user))
    elif zoom.get("account_id"):
        results["zoom"] = {"status": "skipped", "detail": "Auto sync disabled"}

    google_connected = bool(google.get("access_token") or google.get("refresh_token") or google.get("api_key") or google.get("service_account_json"))
    if google_connected and _is_auto_sync_enabled(current_user, "google"):
        await _run("google", sync_google_meetings(days_ahead=30, limit=50, db=db, current_user=current_user))
    elif google_connected:
        results["google"] = {"status": "skipped", "detail": "Auto sync disabled"}

    if ms.get("tenant_id") and _is_auto_sync_enabled(current_user, "microsoft"):
        await _run("microsoft", sync_microsoft_meetings(days_ahead=30, limit=50, db=db, current_user=current_user))
    elif ms.get("tenant_id"):
        results["microsoft"] = {"status": "skipped", "detail": "Auto sync disabled"}

    cleanup = _run_recording_retention_cleanup_for_user(db, current_user)
    results["retention_cleanup"] = {
        "status": "ok",
        "meetings_cleaned": cleanup["meetings_cleaned"],
        "files_deleted": cleanup["files_deleted"],
    }

    try:
        bot_dispatch = await run_auto_join_bots_now(db=db, current_user=current_user)
        results["auto_join_bots"] = bot_dispatch
    except Exception as exc:
        results["auto_join_bots"] = {"status": "error", "detail": str(exc)}

    return {"status": "ok", "results": results}


@router.post("/retention/run-now")
async def run_retention_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup = _run_recording_retention_cleanup_for_user(db, current_user)
    return {"status": "ok", **cleanup}


async def run_integration_auto_sync_for_all_users() -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "users": 0,
        "platform_runs": 0,
        "platform_errors": 0,
        "retention_meetings_cleaned": 0,
        "retention_files_deleted": 0,
    }

    with SessionLocal() as db:
        users = db.execute(select(User)).scalars().all()
        summary["users"] = len(users)

        for user in users:
            integrations = user.integrations or {}
            has_any = bool(
                (integrations.get("zoom") or {}).get("account_id")
                or (integrations.get("google") or {}).get("api_key")
                or (integrations.get("google") or {}).get("service_account_json")
                or (integrations.get("microsoft") or {}).get("tenant_id")
            )
            if not has_any:
                continue

            async def _run(platform: str, coro):
                try:
                    data = await coro
                    summary["platform_runs"] += 1
                    _set_auto_sync_status(
                        db,
                        user,
                        platform,
                        "ok",
                        metrics={
                            "created": data.get("created", 0),
                            "updated": data.get("updated", 0),
                            "skipped": data.get("skipped", 0),
                            "fetched": data.get("fetched", 0),
                        },
                    )
                except Exception as exc:
                    summary["platform_errors"] += 1
                    _set_auto_sync_status(db, user, platform, "error", detail=str(exc))

            if (integrations.get("zoom") or {}).get("account_id") and _is_auto_sync_enabled(user, "zoom"):
                await _run("zoom", sync_zoom_meetings(limit=20, db=db, current_user=user))
            if ((integrations.get("google") or {}).get("api_key") or (integrations.get("google") or {}).get("service_account_json")) and _is_auto_sync_enabled(user, "google"):
                await _run("google", sync_google_meetings(days_ahead=30, limit=50, db=db, current_user=user))
            if (integrations.get("microsoft") or {}).get("tenant_id") and _is_auto_sync_enabled(user, "microsoft"):
                await _run("microsoft", sync_microsoft_meetings(days_ahead=30, limit=50, db=db, current_user=user))

            cleanup = _run_recording_retention_cleanup_for_user(db, user)
            summary["retention_meetings_cleaned"] += cleanup["meetings_cleaned"]
            summary["retention_files_deleted"] += cleanup["files_deleted"]

            try:
                await run_auto_join_bots_now(db=db, current_user=user)
            except Exception:
                pass

    return summary


async def run_retention_enforcement_for_all_users() -> Dict[str, Any]:
    summary = {
        "users": 0,
        "meetings_cleaned": 0,
        "files_deleted": 0,
    }

    with SessionLocal() as db:
        users = db.execute(select(User)).scalars().all()
        summary["users"] = len(users)
        for user in users:
            cleanup = _run_recording_retention_cleanup_for_user(db, user)
            summary["meetings_cleaned"] += cleanup["meetings_cleaned"]
            summary["files_deleted"] += cleanup["files_deleted"]

    return summary

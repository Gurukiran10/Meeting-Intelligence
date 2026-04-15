from typing import Any, Dict, List, Optional

from pydantic import BaseModel


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

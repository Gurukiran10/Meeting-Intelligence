"""
Application Configuration Management
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings"""
    
    # Application
    APP_NAME: str = "Meeting Intelligence Agent"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "syncminds-dev-secret-change-in-production"
    API_V1_PREFIX: str = "/api/v1"
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8001
    
    # Database
    DATABASE_URL: str
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 40
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_EXPIRY: int = 3600
    
    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    
    # JWT
    JWT_SECRET_KEY: str = "syncminds-jwt-dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Groq (fast inference)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Grok (xAI)
    GROK_API_KEY: str = ""
    GROK_BASE_URL: str = "https://api.x.ai/v1"
    GROK_MODEL: str = "grok-2-latest"
    
    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-opus-20240229"
    
    # Whisper
    WHISPER_MODEL: str = "large-v3"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "float16"
    
    # Pinecone
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = "us-west1-gcp"
    PINECONE_INDEX_NAME: str = "meeting-intelligence"
    
    # Google
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""
    
    # Microsoft
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"
    MICROSOFT_REDIRECT_URI: str = ""
    
    # Zoom
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    ZOOM_BOT_JWT: str = ""
    
    # Slack
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_FORCE_TEST_CHANNEL_ONLY: bool = True
    SLACK_TEST_CHANNEL_ID: str = ""
    
    # Linear
    LINEAR_API_KEY: str = ""
    LINEAR_WEBHOOK_SECRET: str = ""
    
    # Jira
    JIRA_URL: str = ""
    JIRA_USERNAME: str = ""
    JIRA_API_TOKEN: str = ""
    
    # Asana
    ASANA_ACCESS_TOKEN: str = ""
    
    # Notion
    NOTION_API_KEY: str = ""
    
    # Email
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "noreply@meetingintel.ai"
    
    # Storage
    STORAGE_TYPE: str = "local"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_BUCKET: str = ""
    AWS_REGION: str = "us-east-1"
    
    # Monitoring
    SENTRY_DSN: str = ""
    PROMETHEUS_PORT: int = 9090
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000
    
    # Meeting Recording
    MAX_RECORDING_DURATION_HOURS: int = 4
    RECORDING_RETENTION_DAYS: int = 90
    AUTO_DELETE_RECORDINGS: bool = True
    
    # Features
    ENABLE_REAL_TIME_ALERTS: bool = True
    ENABLE_PRE_MEETING_BRIEFS: bool = True
    ENABLE_POST_MEETING_SUMMARIES: bool = True
    ENABLE_ACTION_ITEM_TRACKING: bool = True
    ENABLE_ABSENCE_CATCH_UP: bool = True
    ENABLE_MEETING_ANALYTICS: bool = True
    ENABLE_ANALYTICS: bool = True
    ENABLE_AUTO_JOIN: bool = True
    ENABLE_INTEGRATION_AUTO_SYNC: bool = True
    INTEGRATION_AUTO_SYNC_INTERVAL_MINUTES: int = 60
    ENABLE_RETENTION_ENFORCEMENT_JOB: bool = True
    RETENTION_ENFORCEMENT_INTERVAL_MINUTES: int = 360
    ENABLE_AUTO_JOIN_BOT_DISPATCH: bool = True
    # Post-meeting summary & action tracking timing
    POST_MEETING_SUMMARY_DELAY_MINUTES: int = 5
    ACTION_ITEM_REMINDER_48H_HOURS: int = 48
    ACTION_ITEM_REMINDER_DAILY_HOURS: int = 24
    ACTION_ITEM_OVERDUE_ESCALATION_HOURS: int = 168
    
    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3002",
        "http://localhost:3001",
        "http://localhost:3000",
        "http://localhost:8002",
        "http://localhost:8001",
        "http://localhost:8000",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8002",
        "http://127.0.0.1:8001",
        "http://127.0.0.1:8000"
    ]
    ALLOWED_METHODS: List[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    ALLOWED_HEADERS: List[str] = ["*"]
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"
    )


settings = Settings()  # type: ignore

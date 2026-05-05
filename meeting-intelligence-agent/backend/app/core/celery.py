"""
Celery Configuration for Background Tasks
"""
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "meeting_intelligence",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.meeting_processor",
        "app.tasks.action_item_reminders",
        "app.tasks.calendar_auto_join",
        "app.tasks.pre_meeting_brief_task",
        "app.tasks.auto_join",
        "app.tasks.cleanup",
    ],
)

from celery.schedules import crontab

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,

    # Two queues: normal tasks vs. bot sessions.
    # Bot sessions are long-lived (up to 2h) so they get their own worker pool.
    # Start the bot worker with: celery -A app.core.celery:celery_app worker -Q bots -c 4
    task_routes={
        "run_bot_session":       {"queue": "bots"},
        "poll_db_and_auto_join": {"queue": "celery"},  # lightweight scanner
    },

    beat_schedule={
        # DB-based auto-join scanner (no Google Calendar needed)
        "poll-db-auto-join": {
            "task": "poll_db_and_auto_join",
            "schedule": 30.0,
        },
        # Google Calendar-based auto-join (existing, kept for backward compat)
        "poll-calendar-auto-join": {
            "task": "poll_calendar_and_auto_join",
            "schedule": 30.0,
        },
        # Pre-meeting briefs
        "send-pre-meeting-briefs": {
            "task": "send_pre_meeting_briefs",
            "schedule": 60.0,
        },
        # Action item reminders
        "send-action-item-reminders": {
            "task": "send_action_item_reminders",
            "schedule": crontab(minute=0),
        },

        # Stuck meeting cleanup — every 5 minutes
        "cleanup-stuck-meetings": {
            "task": "cleanup_stuck_meetings",
            "schedule": 300.0,
        },
        # Google Calendar webhook renewal — every 6 hours
        "renew-google-webhooks": {
            "task": "renew_google_webhooks",
            "schedule": crontab(minute=0, hour="*/6"),
        },
    },
)


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
    task_time_limit=3600,  # 1 hour
    task_soft_time_limit=3300,  # 55 minutes
    beat_schedule={
        # Poll Google Calendar every 5 minutes and auto-join upcoming Meet sessions
        "poll-calendar-auto-join": {
            "task": "poll_calendar_and_auto_join",
            "schedule": 300.0,  # seconds
        },
        # Send pre-meeting briefs to attendees 30 minutes before each meeting
        "send-pre-meeting-briefs": {
            "task": "send_pre_meeting_briefs",
            "schedule": 300.0,  # every 5 minutes
        },
        # Check action item due dates every hour
        "send-action-item-reminders": {
            "task": "send_action_item_reminders",
            "schedule": crontab(minute=0),  # top of every hour
        },
    },
)


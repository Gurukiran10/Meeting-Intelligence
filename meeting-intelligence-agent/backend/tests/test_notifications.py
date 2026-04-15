import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.services.ai.nlp import MentionDetection
from app.services.mentions import detect_and_store_mentions
from tests.conftest import create_meeting


def test_task_assignment_creates_notification(client, db_session, admin_a, member_a):
    login = client.post("/api/v1/auth/login", data={"username": "admina", "password": "password123"})
    assert login.status_code == 200

    meeting = create_meeting(db_session, admin_a.organization, admin_a, title="Notify Meeting")

    create_response = client.post(
        "/api/v1/action-items/",
        json={
            "title": "Prepare deck",
            "meeting_id": str(meeting.id),
            "assigned_to_user_id": str(member_a.id),
            "priority": "high",
        },
    )
    assert create_response.status_code == 201, create_response.text

    notifications = db_session.execute(
        select(Notification).where(Notification.user_id == member_a.id)
    ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].type == "task_assigned"
    assert "Prepare deck" in notifications[0].message


@pytest.mark.asyncio
async def test_mention_creates_and_reads_notification(db_session, admin_a, member_a, monkeypatch):
    meeting = create_meeting(db_session, admin_a.organization, admin_a, title="Mention Meeting")

    async def fake_detect_mentions(*args, **kwargs):
        return [
            MentionDetection(
                user_name="Member A",
                mention_type="direct",
                text="Member A should review the final notes.",
                context="Member A should review the final notes.",
                relevance_score=95.0,
                is_action_item=False,
                is_question=False,
            )
        ]

    monkeypatch.setattr("app.services.mentions.nlp_service.detect_mentions", fake_detect_mentions)

    created_mentions = await detect_and_store_mentions(
        db=db_session,
        meeting=meeting,
        transcript_text="Member A should review the final notes.",
        candidate_users=[admin_a, member_a],
        send_real_time_alerts=False,
    )

    assert len(created_mentions) == 1

    notifications = db_session.execute(
        select(Notification).where(
            Notification.user_id == member_a.id,
            Notification.type == "mention",
        )
    ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].is_read is False

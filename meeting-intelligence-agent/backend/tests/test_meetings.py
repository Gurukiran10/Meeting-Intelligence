from datetime import datetime, timedelta
from io import BytesIO

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.action_item import ActionItem as ActionItemModel
from app.models.meeting import Meeting
from app.models.mention import Mention
from app.models.transcript import Transcript
from app.services.ai.nlp import ActionItem, Decision, MeetingSummary, MentionDetection
from app.services.ai.transcription import TranscriptionResult, TranscriptionSegment
from app.tasks import meeting_processor


def test_create_meeting_and_upload_recording(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    signup = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "owner@example.com",
            "username": "owner",
            "full_name": "Owner User",
            "password": "password123",
            "organization_name": "Owner Org",
            "organization_slug": "owner-org",
            "create_organization": True,
        },
    )
    assert signup.status_code == 201

    meeting_response = client.post(
        "/api/v1/meetings/",
        json={
            "title": "Weekly Sync",
            "description": "Planning session",
            "platform": "manual",
            "scheduled_start": "2026-04-15T10:00:00",
            "scheduled_end": "2026-04-15T11:00:00",
            "attendee_ids": [],
            "agenda": {"topics": ["Roadmap"]},
            "tags": ["planning"],
        },
    )
    assert meeting_response.status_code == 201, meeting_response.text
    meeting_id = meeting_response.json()["id"]

    scheduled_calls: list[tuple[str, str]] = []

    def fake_background_processor(meeting_id_arg, recording_path_arg):
        scheduled_calls.append((str(meeting_id_arg), recording_path_arg))

    monkeypatch.setattr("app.api.v1.endpoints.meetings.process_meeting_recording_background", fake_background_processor)

    upload_response = client.post(
        f"/api/v1/meetings/{meeting_id}/upload",
        files={"file": ("sample.wav", BytesIO(b"fake-audio"), "audio/wav")},
    )

    assert upload_response.status_code == 200, upload_response.text
    assert upload_response.json()["status"] == "processing"
    assert scheduled_calls
    assert scheduled_calls[0][0] == meeting_id
    assert tmp_path.joinpath(scheduled_calls[0][1]).exists()

    with SessionLocal() as db:
        stored_meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one()
        assert stored_meeting.transcription_status == "processing"
        assert stored_meeting.recording_path == scheduled_calls[0][1]


@pytest.mark.asyncio
async def test_ai_processing_persists_transcript_actions_and_mentions(db_session, org_a, admin_a, member_a, monkeypatch, tmp_path):
    meeting = Meeting(
        organization_id=org_a.id,
        created_by=admin_a.id,
        organizer_id=admin_a.id,
        title="AI Processing Test",
        platform="manual",
        scheduled_start=datetime.utcnow(),
        scheduled_end=datetime.utcnow() + timedelta(hours=1),
        attendee_ids=[str(member_a.id)],
        attendee_count=1,
        status="scheduled",
    )
    db_session.add(meeting)
    db_session.commit()
    db_session.refresh(meeting)

    recording_path = tmp_path / "meeting.wav"
    recording_path.write_bytes(b"audio")

    async def fake_transcribe_audio(*args, **kwargs):
        return TranscriptionResult(
            segments=[
                TranscriptionSegment(start=0.0, end=3.0, text="Amit will prepare the proposal by Friday.", speaker="S1", confidence=0.98),
                TranscriptionSegment(start=3.0, end=5.0, text="We need Member A to review the summary.", speaker="S2", confidence=0.95),
            ],
            language="en",
            duration=5.0,
        )

    async def fake_generate_summary(*args, **kwargs):
        return MeetingSummary(
            executive_summary="The team aligned on the next proposal draft.",
            key_points=["Proposal needs an owner", "Review is required"],
            decisions=[
                Decision(
                    decision="Ship the proposal on Friday",
                    reasoning="Customer deadline is Friday",
                    alternatives=[],
                    decision_maker="Admin A",
                    is_reversible=False,
                    impact_level="high",
                )
            ],
            action_items=[
                ActionItem(
                    title="Prepare proposal",
                    description="Draft the customer proposal",
                    owner="Member A",
                    due_date="2026-04-18T10:00:00",
                    priority="high",
                    confidence=0.91,
                )
            ],
            discussion_topics=["Proposal"],
            sentiment="positive",
            sentiment_score=0.72,
        )

    async def fake_detect_mentions(*args, **kwargs):
        return [
            MentionDetection(
                user_name="Member A",
                mention_type="action_assignment",
                text="We need Member A to review the summary.",
                context="We need Member A to review the summary.",
                relevance_score=96.0,
                is_action_item=True,
                is_question=False,
            )
        ]

    monkeypatch.setattr(meeting_processor.transcription_service, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(meeting_processor.nlp_service, "generate_summary", fake_generate_summary)
    monkeypatch.setattr("app.services.mentions.nlp_service.detect_mentions", fake_detect_mentions)

    await meeting_processor._process_meeting_async(meeting.id, str(recording_path))

    db_session.expire_all()
    refreshed_meeting = db_session.execute(select(Meeting).where(Meeting.id == meeting.id)).scalar_one()
    transcripts = db_session.execute(select(Transcript).where(Transcript.meeting_id == meeting.id)).scalars().all()
    action_items = db_session.execute(select(ActionItemModel).where(ActionItemModel.meeting_id == meeting.id)).scalars().all()
    mentions = db_session.execute(select(Mention).where(Mention.meeting_id == meeting.id)).scalars().all()

    assert refreshed_meeting.status == "completed"
    assert refreshed_meeting.transcription_status == "completed"
    assert refreshed_meeting.analysis_status == "completed"
    assert refreshed_meeting.summary == "The team aligned on the next proposal draft."
    assert len(transcripts) == 2
    assert len(action_items) == 1
    assert str(action_items[0].assigned_to_user_id) == str(member_a.id)
    assert action_items[0].confidence_score == 0.91
    assert len(mentions) == 1
    assert str(mentions[0].user_id) == str(member_a.id)
    assert mentions[0].organization_id == org_a.id

import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


TEST_DB_PATH = Path(__file__).resolve().parent / "test_suite.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["ENABLE_INTEGRATION_AUTO_SYNC"] = "false"
os.environ["ENABLE_RETENTION_ENFORCEMENT_JOB"] = "false"
os.environ["DEBUG"] = "true"

from app.main import app  # noqa: E402
from app.core.database import Base, SessionLocal, engine, get_db  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models.action_item import ActionItem  # noqa: E402
from app.models.meeting import Meeting  # noqa: E402
from app.models.mention import Mention  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import User  # noqa: E402


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_database() -> Iterator[None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_factory():
    @contextmanager
    def _factory() -> Iterator[TestClient]:
        with TestClient(app) as test_client:
            yield test_client

    return _factory


def create_organization(db_session, name: str, slug: str) -> Organization:
    organization = Organization(name=name, slug=slug)
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)
    return organization


def create_user(
    db_session,
    organization: Organization,
    *,
    email: str,
    username: str,
    full_name: str,
    role: str = "member",
    password: str = "password123",
) -> User:
    user = User(
        organization_id=organization.id,
        email=email,
        username=username,
        full_name=full_name,
        hashed_password=get_password_hash(password),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def create_meeting(
    db_session,
    organization: Organization,
    organizer: User,
    *,
    title: str = "Team Sync",
    attendee_ids: list[str] | None = None,
) -> Meeting:
    start = datetime.utcnow()
    meeting = Meeting(
        organization_id=organization.id,
        created_by=organizer.id,
        organizer_id=organizer.id,
        title=title,
        platform="manual",
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        attendee_ids=attendee_ids or [],
        attendee_count=len(attendee_ids or []),
        status="scheduled",
    )
    db_session.add(meeting)
    db_session.commit()
    db_session.refresh(meeting)
    return meeting


def create_action_item(
    db_session,
    organization: Organization,
    meeting: Meeting,
    assigned_to_user: User,
    *,
    title: str = "Follow up",
) -> ActionItem:
    item = ActionItem(
        organization_id=organization.id,
        meeting_id=meeting.id,
        assigned_to_user_id=assigned_to_user.id,
        title=title,
        status="open",
        priority="medium",
        extraction_method="manual",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def create_mention(
    db_session,
    organization: Organization,
    meeting: Meeting,
    user: User,
    *,
    text: str = "Need Amit to review this",
) -> Mention:
    mention = Mention(
        organization_id=organization.id,
        meeting_id=meeting.id,
        user_id=user.id,
        mention_type="direct",
        mentioned_text=text,
        relevance_score=90.0,
        urgency_score=60.0,
        notification_read=False,
        confidence=0.9,
    )
    db_session.add(mention)
    db_session.commit()
    db_session.refresh(mention)
    return mention


@pytest.fixture
def org_a(db_session) -> Organization:
    return create_organization(db_session, "Org A", "org-a")


@pytest.fixture
def org_b(db_session) -> Organization:
    return create_organization(db_session, "Org B", "org-b")


@pytest.fixture
def admin_a(db_session, org_a) -> User:
    return create_user(
        db_session,
        org_a,
        email="admin-a@example.com",
        username="admina",
        full_name="Admin A",
        role="admin",
    )


@pytest.fixture
def member_a(db_session, org_a) -> User:
    return create_user(
        db_session,
        org_a,
        email="member-a@example.com",
        username="membera",
        full_name="Member A",
    )


@pytest.fixture
def member_b(db_session, org_b) -> User:
    return create_user(
        db_session,
        org_b,
        email="member-b@example.com",
        username="memberb",
        full_name="Member B",
    )


@pytest.fixture
def auth_login(client: TestClient):
    def _login(username: str, password: str = "password123") -> TestClient:
        response = client.post(
            "/api/v1/auth/login",
            data={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        return client

    return _login

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.invite import Invite
from app.models.user import User


def test_invite_creation_and_acceptance(client, db_session, admin_a):
    login = client.post("/api/v1/auth/login", data={"username": "admina", "password": "password123"})
    assert login.status_code == 200

    invite_response = client.post("/api/v1/org/invite", json={"email": "amit@example.com"})
    assert invite_response.status_code == 201, invite_response.text
    invite_data = invite_response.json()
    assert invite_data["status"] == "pending"
    assert "invite=" in invite_data["invite_link"]

    accept_client = client.__class__(client.app)
    try:
        accept_response = accept_client.post(
            "/api/v1/org/accept-invite",
            json={
                "token": invite_data["token"],
                "full_name": "Amit",
                "username": "amit",
                "password": "password123",
            },
        )
        assert accept_response.status_code == 200, accept_response.text
        assert accept_response.json()["organization"]["slug"] == "org-a"

        me_response = accept_client.get("/api/v1/auth/me")
        assert me_response.status_code == 200
        assert me_response.json()["username"] == "amit"
    finally:
        accept_client.close()

    invite = db_session.execute(select(Invite).where(Invite.email == "amit@example.com")).scalar_one()
    user = db_session.execute(select(User).where(User.email == "amit@example.com")).scalar_one()
    assert invite.status == "accepted"
    assert str(user.organization_id) == str(admin_a.organization_id)


def test_expired_invite_token_rejected(client, db_session, admin_a):
    login = client.post("/api/v1/auth/login", data={"username": "admina", "password": "password123"})
    assert login.status_code == 200

    invite_response = client.post("/api/v1/org/invite", json={"email": "expired@example.com"})
    assert invite_response.status_code == 201
    token = invite_response.json()["token"]

    invite = db_session.execute(select(Invite).where(Invite.token == token)).scalar_one()
    invite.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()

    accept_response = client.post(
        "/api/v1/org/accept-invite",
        json={
            "token": token,
            "full_name": "Expired User",
            "username": "expireduser",
            "password": "password123",
        },
    )

    assert accept_response.status_code == 400
    assert accept_response.json()["detail"] == "Invite has expired"

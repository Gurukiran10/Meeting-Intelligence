from app.api.v1.endpoints.auth import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME


def test_signup_login_logout_flow(client):
    signup_response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "gk@example.com",
            "username": "gk",
            "full_name": "GK",
            "password": "password123",
            "organization_name": "GK Tech",
            "organization_slug": "gk-tech",
            "create_organization": True,
        },
    )

    assert signup_response.status_code == 201, signup_response.text
    assert ACCESS_COOKIE_NAME in signup_response.cookies
    assert REFRESH_COOKIE_NAME in signup_response.cookies
    assert signup_response.json()["user"]["organization"]["slug"] == "gk-tech"

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "gk"

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204

    unauthorized_me = client.get("/api/v1/auth/me")
    assert unauthorized_me.status_code == 401


def test_invalid_login_rejected(client, db_session, org_a):
    from tests.conftest import create_user

    create_user(
        db_session,
        org_a,
        email="amit@example.com",
        username="amit",
        full_name="Amit",
        password="password123",
    )

    response = client.post(
        "/api/v1/auth/login",
        data={"username": "amit", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"


def test_login_unexpected_error_returns_500(client, db_session, org_a, monkeypatch):
    from tests.conftest import create_user
    from app.api.v1.endpoints import auth as auth_endpoints

    create_user(
        db_session,
        org_a,
        email="crash@example.com",
        username="crash",
        full_name="Crash Test",
        password="password123",
    )

    def crash_verify_password(*args, **kwargs):
        raise RuntimeError("bcrypt failure")

    monkeypatch.setattr(auth_endpoints, "verify_password", crash_verify_password)

    response = client.post(
        "/api/v1/auth/login",
        data={"username": "crash", "password": "password123"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Unable to process login right now"

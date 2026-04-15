from tests.conftest import create_action_item, create_meeting, create_mention


def test_org_isolation_for_meetings(client_factory, db_session, org_a, org_b, member_a, member_b):
    meeting_a = create_meeting(db_session, org_a, member_a, title="Org A Meeting")
    create_meeting(db_session, org_b, member_b, title="Org B Meeting")

    with client_factory() as client_b:
        login = client_b.post("/api/v1/auth/login", data={"username": "memberb", "password": "password123"})
        assert login.status_code == 200

        detail_response = client_b.get(f"/api/v1/meetings/{meeting_a.id}")
        assert detail_response.status_code == 403

        list_response = client_b.get("/api/v1/meetings")
        assert list_response.status_code == 200
        titles = [meeting["title"] for meeting in list_response.json()]
        assert "Org A Meeting" not in titles


def test_org_isolation_for_action_items(client_factory, db_session, org_a, org_b, member_a, member_b):
    meeting_a = create_meeting(db_session, org_a, member_a, title="Cross Org Action Meeting")
    item_a = create_action_item(db_session, org_a, meeting_a, member_a, title="Org A Task")
    create_meeting(db_session, org_b, member_b, title="Org B Action Meeting")

    with client_factory() as client_b:
        login = client_b.post("/api/v1/auth/login", data={"username": "memberb", "password": "password123"})
        assert login.status_code == 200

        detail_response = client_b.get(f"/api/v1/action-items/{item_a.id}")
        assert detail_response.status_code == 404

        list_response = client_b.get("/api/v1/action-items")
        assert list_response.status_code == 200
        titles = [item["title"] for item in list_response.json()]
        assert "Org A Task" not in titles


def test_org_isolation_for_mentions(client_factory, db_session, org_a, org_b, member_a, member_b):
    meeting_a = create_meeting(db_session, org_a, member_a, title="Cross Org Mention Meeting")
    mention_a = create_mention(db_session, org_a, meeting_a, member_a, text="Need Member A on this")
    create_meeting(db_session, org_b, member_b, title="Org B Mention Meeting")

    with client_factory() as client_b:
        login = client_b.post("/api/v1/auth/login", data={"username": "memberb", "password": "password123"})
        assert login.status_code == 200

        detail_response = client_b.get(f"/api/v1/mentions/{mention_a.id}")
        assert detail_response.status_code == 404

        list_response = client_b.get("/api/v1/mentions")
        assert list_response.status_code == 200
        assert list_response.json() == []

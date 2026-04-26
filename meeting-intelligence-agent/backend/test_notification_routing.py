import asyncio
from unittest.mock import patch, MagicMock

# Create mock objects
class MockUser:
    def __init__(self, id, full_name, slack_user_id=None):
        self.id = id
        self.full_name = full_name
        self.slack_user_id = slack_user_id

class MockActionItem:
    def __init__(self, title, owner, due_date=None):
        self.title = title
        self.owner = owner
        self.due_date = due_date

class MockSummary:
    def __init__(self, action_items):
        self.action_items = action_items

class MockMeeting:
    def __init__(self, id, title, organization_id):
        self.id = id
        self.title = title
        self.organization_id = organization_id

class MockMentionData:
    def __init__(self, mention_type="direct"):
        self.mention_type = mention_type

class MockMention:
    def __init__(self, id):
        self.id = id

# The logic extracted from meeting_processor.py
def process_mention(matched_user, mention_name, summary, meeting, mention_data, mention, db=None):
    output = []
    
    # Mock Slack DM
    def mock_send_slack_dm(slack_id, msg):
        output.append(f"SLACK DM to {slack_id}: {msg}")
        
    # Mock create_notification
    def mock_create_notification(db, user_id, organization_id, notification_type, message, notification_metadata):
        output.append(f"IN-APP NOTIF for {user_id}: {message}")

    user_tasks = [
        a for a in summary.action_items 
        if a.owner == matched_user.full_name or a.owner == mention_name
    ]

    message = f"👋 You were mentioned in a meeting\n\n📌 Meeting: {meeting.title}\n📎 View: http://localhost:3002/meetings/{meeting.id}\n"
    
    if user_tasks:
        task = user_tasks[0]
        message += f"\n📌 Task: {task.title}"
        if getattr(task, 'due_date', None):
            message += f"\n⏰ Deadline: {task.due_date}"

    if matched_user.slack_user_id:
        mock_send_slack_dm(matched_user.slack_user_id, message)
    else:
        mock_create_notification(
            db,
            user_id=matched_user.id,
            organization_id=meeting.organization_id,
            notification_type="mention",
            message=message,
            notification_metadata={
                "mention_id": str(mention.id),
                "meeting_id": str(meeting.id),
                "mention_type": mention_data.mention_type or "direct",
                "source": "ai_extraction",
            },
        )
    return output

def run_tests():
    # User A has Slack
    user_a = MockUser(id=1, full_name="User A", slack_user_id="U12345")
    # User B has no Slack
    user_b = MockUser(id=2, full_name="User B")
    
    meeting = MockMeeting(id=101, title="Weekly Sync", organization_id=5)
    summary = MockSummary([MockActionItem("Update docs", "User A", "2026-05-01")])
    mention_data = MockMentionData()
    mention = MockMention(id=999)

    print("Test 1: User A with Slack and Action Item")
    out1 = process_mention(user_a, "User A", summary, meeting, mention_data, mention)
    print(out1[0])
    
    print("\nTest 2: User B without Slack and no Action Item")
    out2 = process_mention(user_b, "User B", summary, meeting, mention_data, mention)
    print(out2[0])

if __name__ == "__main__":
    run_tests()

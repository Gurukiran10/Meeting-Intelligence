"""
Simple Slack Notification Service
"""
import os
import logging
from slack_sdk import WebClient

logger = logging.getLogger(__name__)

# Initialize client using token from environment
client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

def send_slack_dm(slack_user_id: str, message: str):
    """
    Sends a direct message to a Slack user safely.
    Does not crash on failure.
    """
    try:
        if not slack_user_id:
            return
            
        client.chat_postMessage(
            channel=slack_user_id,
            text=message
        )
    except Exception as e:
        logger.warning(f"Slack error sending DM to {slack_user_id}: {e}")
